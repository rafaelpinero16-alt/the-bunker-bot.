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
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from database.database import (
    get_or_create_user, get_user_groups, get_user_channels,
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
    # 🚨 ULTRA PRO — Panel de Élite & Módulos Corporativos
    get_panic_status, set_panic_status,
    get_shield_status, set_shield_status,
    get_podcast_status, set_podcast_status,
    get_service_msgs_mode, set_service_msgs_mode,
    get_tips_config, set_tips_config,
    get_sentinel_payload_config, set_sentinel_payload_config,
    # 💎 Módulos de Canales & Membresías
    get_channel_settings, set_channel_settings,
    get_channel_plans, get_active_subscribers_count,
    create_channel_plan, delete_channel_plan,
    get_night_mode_config,
    set_night_mode_config,
    # 🧹 Ghost Purge de Élite
    get_ghost_purge_config, set_ghost_purge_config,
    check_can_free_purge, update_ghost_purge_scan_time
)
from assistant import (
    register_or_update_sentinel, disconnect_sentinel,
    start_phone_auth, verify_phone_code, verify_2fa_password, cancel_phone_auth,
    engage_screen_shield, disengage_screen_shield,
    engage_podcast_ducking, disengage_podcast_ducking
)
from .groups import (
    execute_raid_lockdown, lift_raid_lockdown,
    add_speaker_to_queue, pop_next_speaker, clear_speaker_queue, get_speaker_queue
)

router = Router()
logger = logging.getLogger("user_private_handler")


class CallbackAutoAnswerMiddleware(BaseMiddleware):
    """
    Telegram admite UNA sola respuesta por callback_query.
    Garantiza que el spinner del botón se libere siempre sin colisiones de respuesta doble.
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


def fire_and_forget_auto_delete(messages: list, delay: int = 60):
    """Worker no bloqueante que auto-destruye mensajes tras 60s en DM para mantener la consola limpia."""
    async def _del_task():
        await asyncio.sleep(delay)
        for m in messages:
            if m:
                try:
                    await m.delete()
                except Exception:
                    pass
    asyncio.create_task(_del_task())


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
    global MASTER_BOT_ID
    MASTER_BOT_ID = int(bot_id)

MASTER_BOT_USERNAME = ""

def set_master_bot_username(username: str) -> None:
    global MASTER_BOT_USERNAME
    MASTER_BOT_USERNAME = (username or "").lstrip("@")

def get_master_bot_username() -> str:
    return MASTER_BOT_USERNAME

def _resolve_master_bot_id() -> int:
    if MASTER_BOT_ID:
        return MASTER_BOT_ID
    head = os.getenv("BOT_TOKEN", "").split(":", 1)[0].strip()
    return int(head) if head.isdigit() else 0

def is_clone_bot(bot: Bot) -> bool:
    master_id = _resolve_master_bot_id()
    return bool(master_id) and bot.id != master_id

def _call_clone_trigger(name: str, token: str) -> None:
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
NIGHT_STATES = {}
DB_REG_STATES = {}
MOD_TARGET_STATES = {}
MIC_VIP_STATES = {}
MIC_TAG_STATES = {}
GROUP_MIC_PRICE = {}
GROUP_VIP_TAG = {}
PODCAST_DUCK_STATES = {}
SPEAKER_PRICE_STATES = {}
GROUP_DUCK_LEVEL = {}
GROUP_SPEAKER_PRICE = {}
TIPS_AMOUNT_STATES = {}
TIPS_TARGET_STATES = {}
SENTINEL_PAYLOAD_TEXT_STATES = {}
SENTINEL_PAYLOAD_MEDIA_STATES = {}
SENTINEL_PAYLOAD_AUTODEL_STATES = {}
CHAN_PLAN_STATES = {}

CHAN_PLAN_TIER_LIMITS = {"free": 1, "pro": 3, "ultra_pro": 10}

FILTER_MAP = {
    "tglinks": "tg_links", "fwdchan": "fwd_channels", "fwdusr": "fwd_users",
    "fwdgrp": "fwd_groups", "fwdbot": "fwd_bots", "quotes": "quotes", "weblinks": "web_links"
}

TEXTS = {
    "en": {
        "owner_only_alert": "⛔ Access Denied: This command center is strictly restricted to the community/channel Owner.",
        "welcome": (
            "🏴‍☠️ <b>Welcome to the Inner Circle, {name}.</b>\n\n"
            "I am <b>The Bunker Bot</b>, the architectural security core designed by <b>Master Tom</b>. Within this domain, you wield absolute authority to forge order out of chaos.\n\n"
            "Deploy me to your groups for elite perimeter defense, or to your channels to manage automated subscriptions and live studio moderation.\n\n"
            "Select an option below to audit tactical modules or access the Command Center.\n\n"
            "🛡️ <i>Powered by <b>Cloud Media Management</b>.</i>"
        ),
        "btn_add_group": "➕ Add to a Group",
        "btn_add_channel": "📢 Add to a Channel",
        "btn_settings": "⚙️ Group Settings",
        "btn_chsettings": "📡 Channel Settings",
        "btn_saas": "⚡ Command Center",
        "btn_id": "🛠️ My ID & Status",
        "btn_support": "🆘 Support",
        "btn_info": "ℹ️ Information",
        "btn_how_works": "📖 How The Bunker Works",
        "settings_main": (
    "🛡️ <b>Tactical Community Command (Groups)</b>\n\n"
    "Take total perimeter control over your community:\n\n"
    "• 🤖 Alphanumeric Captcha checkpoint.\n"
    "• 🔒 Content Locks and Anti-Spam shields.\n"
    "• 🎙️ Voice Sentinel and acoustic ducking.\n"
    "• 💰 Telegram Stars Monetization with custom VIP tags.\n\n"
    "<i>Select the group below you wish to audit and shield:</i>\n\n"
    "© <i>Cloud Media Management</i>"
),
"chsettings_main": (
    "📡 <b>Live Studio & Membership Command (Channels)</b>\n\n"
    "Direct broadcasting studio and subscriber monetizer:\n\n"
    "• 🎙️ <b>Live Sentinel:</b> Instant mic unmuting upon 'Raise Hand' verification.\n"
    "• 💎 <b>Paywalled Invites:</b> Cryptographic single-use links burned on join.\n"
    "• ⏳ <b>Subscription Auditor:</b> Auto-renewal alerts & automated kick for unpaid members.\n"
    "• ⭐ <b>Live Stars Tipping:</b> Direct monetized broadcasts.\n\n"
    "<i>Select the channel below you wish to manage:</i>\n\n"
    "© <i>Cloud Media Management</i>"
),
        "support_main": (
            "🆘 <b>Official Tactical Support</b>\n\n"
            "For direct assistance, elite passes, or custom architectures, contact our Chief Architect:\n\n"
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
            "• <b>Version:</b> 6.0 (Dual Group & Channel OS Core)\n"
            "• <b>Architect:</b> Master Tom\n"
            "• <b>Tactical Focus:</b> Multi-Sentinel Grid, Channel Membreships, Native Badges, and Absolute Security.\n\n"
            "🛡️ <i>Developed and supported by <b>Cloud Media Management</b>.</i>"
        ),
        "group_panel_title": "🛡️ <b>Security Matrix:</b> {group_name}\n\nSelect a tactical module to alter community parameters.",
        "channel_panel_title": "📡 <b>Broadcast Studio:</b> {channel_name}\n\nSelect a module to manage lives, memberships, or studio automation.",
        "pay_pro_title": (
            "⭐ <b>PRO Plan Subscription — {group_name} (300 Stars)</b>\n\n"
            "Upgrade your community to elite operational status:\n\n"
            "• ⚡ <b>Unlimited Bot Commands:</b> Bypass the 3 daily uses limit.\n"
            "• 🗑️ <b>Automated Purge Center:</b> Service logs and chat clutter cleanup.\n"
            "• 🤖 <b>Advanced Captcha Pro:</b> Custom welcome copy and timeout parameters.\n"
            "• 🛡️ <b>Granular Anti-Spam:</b> Advanced shielding against channels, bots, and links.\n\n"
            "<i>Select your payment gateway below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>ULTRA PRO Subscription — {group_name} (600 Stars)</b>\n\n"
            "Total command, decentralized automation, and high-tier monetization:\n\n"
            "• 🌟 <b>All PRO Plan features included.</b>\n"
            "• 🧬 <b>Bot Clone Architecture:</b> Run an exclusive replica under your own token.\n"
            "• 🎙️ <b>Dedicated Voice Sentinel:</b> Link your account as a 24/7 voice mod via phone.\n"
            "• 🏷️ <b>Native VIP Tag Assignment:</b> Immovable badges (VIP 24/7) on Stars tips.\n"
            "• 🔇 <b>Smart AutoLower Radar:</b> Unverified mics get dialed down to 2% in milliseconds.\n"
            "• 💰 <b>Telegram Stars Monetization:</b> 100% revenue into your balance.\n"
            "• 📢 <b>Channel Membreships Engine:</b> Single-use cryptographic invite links & auto-kick.\n\n"
            "<i>Select your payment gateway below:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_back": "🔙 Back to Main Menu",
        "btn_back_settings": "🔙 Back to Groups",
        "btn_back_chsettings": "🔙 Back to Channels",
        "btn_back_group": "🔙 Group Panel",
        "btn_back_channel": "🔙 Channel Panel",
        "btn_back_captcha": "🔙 Volver a Captcha",
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
            "• 🎙️ <b>/mic_vip:</b> VIP Microphone 24h pass pricing in Stars & Custom Tag.\n"
            "• ⭐ <b>Tips & Donations:</b> Channel tips & custom donation presets.\n\n"
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
        "captcha_action_title": "⚖️ <b>Punishment Configuration</b>\n\n• <b>Active Punishment:</b> {mode_name} 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "antispam_main_title": (
            "✉️ <b>Granular Anti-Spam Matrix</b>\n\n"
            "• 📘 <b>Telegram Links:</b> {st_tg}\n"
            "• 📥 <b>Forwards Shield:</b> {st_fwd}\n"
            "• 💭 <b>Quotes Filter:</b> {st_q}\n"
            "• 🔗 <b>Internet Links:</b> {st_web}\n"
            "• 🗑️ <b>Delete Spam:</b> {st_del}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locks_main_title": (
            "🔒 <b>Locks & Restrictions</b>\n\n"
            "• <b>Media (Photos/Videos):</b> {media}\n"
            "• <b>Stickers & GIFs:</b> {stickers}\n"
            "• <b>Web Links:</b> {links}\n"
            "• <b>Bot Commands:</b> {commands}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "warns_main_title": (
            "⚠️ <b>Tactical Warnings (Warns Matrix)</b>\n\n"
            "• <b>Strike Limit:</b> {limit} warnings\n"
            "• <b>Automated Punishment:</b> {action}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "delmsgs_main_title": (
            "🗑️ <b>Advanced Message Purge Center</b>\n\n"
            "• <b>Tier Status:</b> {tier_display}\n"
            "• <b>Privilege / Quota:</b> {quota_desc}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antiflood_main_title": "🗣️ <b>Anti-Flood Shield</b>\n\n<b>Threshold:</b> {msgs} msgs in {time}s.\n<b>Active Action:</b> {action}\n<b>Delete Messages:</b> {delete_st}",
        "forwards_panel": "🌊 <b>Forwards Shield Configuration</b>\n\nControl what forwarded transmissions are blocked in the community:",
        "clone_main_title": (
            "🧬 <b>Clone & Dedicated Sentinel Architecture</b>\n\n"
            "• <b>License:</b> {tier}\n"
            "• <b>Bot Clone:</b> {status}\n"
            "• <b>Dedicated Sentinel:</b> {sentinel_status}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "botfather_guide": "🔑 <b>How to Connect Your Bot Clone</b>\n\nSend <code>/newbot</code> to @BotFather and paste the token here.",
        "sentinel_phone_guide": "🎙️ <b>Connect Dedicated Sentinel</b>\n\nSend your phone number with country code (e.g. <code>+12025550143</code>).",
        "captcha_saved": "✅ <b>Captcha message saved successfully!</b>\n\n<i>(This prompt will self-destruct in 60s)</i>",
        "token_verifying": "🔄 <b>Verifying bot token with Telegram servers...</b>",
        "token_success": "✅ <b>Token received and verified successfully!</b>",
        "token_error": "❌ <b>Invalid bot token.</b>",
        "phone_requesting": "🔄 <b>Requesting official Telegram code...</b>",
        "phone_sent": "📩 <b>Official Code Sent!</b>\n\nEnter the numerical code received below:",
        "phone_error": "❌ <b>Error sending code:</b> {reason}",
        "code_verifying": "🔄 <b>Verifying code and authorizing Sentinel...</b>",
        "sentinel_success": "💎 <b>Own Sentinel Connected Successfully!</b>",
        "sentinel_error": "❌ <b>Error initializing session.</b>",
        "twofa_required": "🔐 <b>Two-Step Verification (2FA) Required</b>\n\nEnter your password:",
        "code_invalid": "❌ <b>Invalid or expired code.</b>",
        "twofa_verifying": "🔄 <b>Validating 2FA password...</b>",
        "twofa_invalid": "❌ <b>Incorrect 2FA password.</b>",
        "sched_updated": "✅ <b>Schedule Updated!</b> (Start: {start} | End: {end})",
        "sched_err": "⚠️ Invalid format. Use HH:MM-HH:MM (e.g. <code>20:00-23:30</code>).",
        "wl_success": "✅ <b>User successfully whitelisted!</b> (ID: <code>{target_id}</code>)",
        "id_err": "⚠️ <b>Invalid ID.</b>",
        "bl_success": "✅ <b>Term blacklisted!</b> (<code>{word}</code>)",
        "dir_ban": "🚫 <b>Ban Directive Executed!</b> (ID: <code>{target_id}</code>)",
        "dir_kick": "👢 <b>Kick Directive Executed!</b> (ID: <code>{target_id}</code>)",
        "dir_mute": "🔇 <b>Mute Directive Executed!</b> (ID: <code>{target_id}</code>)",
        "dir_unmute": "🔊 <b>Privileges Restored!</b> (ID: <code>{target_id}</code>)",
        "dir_err": "❌ <b>Error executing directive:</b> {ex}",
        "mic_updated": "⭐ <b>VIP Mic Rate Updated to {price_val} Stars!</b>",
        "mic_err": "⚠️ Enter a positive integer.",
        "tag_updated": "🏷️ <b>VIP Tag Updated to: {text_input}</b>",
        "tag_err": "⚠️ Tag must be between 1 and 16 characters.",
        "mod_id_err": "⚠️ <b>Invalid Target.</b>",
        "btn_cancel_ret": "❌ Cancel & Return",
        "btn_retry": "🔄 Retry",
        "op_canceled": "Operation canceled and memory cleared.",
        "sentinel_disc": "🛑 Own Sentinel disconnected successfully.",
        "clone_disc": "🛑 Bot Clone disconnected successfully.",
        "tag_pro_req": "💎 Native tag editing requires ULTRA PRO tier.",
        "al_updated_1": "AutoLower updated 🟢",
        "al_updated_0": "AutoLower disabled 🔴",
        "mic_alert_set": "Rate set to {price_int} Stars ⭐",
        "wl_menu": "⚪ <b>Directive: Tactical Whitelist</b>\n\nRegistered identities receive <b>Absolute Immunity</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_menu": "⚫ <b>Directive: Global Blacklist</b>\n\nForbidden terms trigger auto-purge and warnings.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_menu": "📹 <b>Camera & Video Chat Supervision</b>\n\nStream stability audit active.\n\n🛡️ <i>Cloud Media Management</i>",
        "al_menu": "⚙️ <b>Remote Control: Video Chat AutoLower</b>\n\n• <b>Current Status:</b> {status_str}\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_menu": "🎙️ <b>VIP Microphone Pass (Stars Monetization)</b>\n\n• <b>Current Rate:</b> <code>{curr_price} Stars</code>\n• <b>Tag:</b> <code>{curr_tag}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_menu_prompt": "🏷️ <b>Native VIP Tag Editor</b> (Max 16 chars):",
        "mod_ask_time": "⚡ <b>Moderation Directive: /{sub_cmd}</b>\n\nSelect duration:",
        "mod_ask_target": "🎯 <b>Target Configuration — /{sub_cmd_upper}</b>\n\nSend @username or numeric ID:",
        "reg_ask": "📝 <b>Database Registration</b>\n\nSend {target_name}:",
        "reg_ask_wl": "the user ID for Whitelist",
        "reg_ask_bl": "the forbidden term for Blacklist",
        "vcsched_prompt": "⏰ <b>VC Schedule Configuration</b> (e.g. <code>20:00-23:30</code>):",
        "mic_custom_prompt": "⭐ <b>Custom Stars Rate</b>\n\nSend Stars amount per 24h pass:",
        "vcsched_main": "🗓️ <b>Voice Chat Scheduler (ULTRA PRO)</b>\n\n• <b>Status:</b> {st_badge}\n• <b>Schedule:</b> <code>{start} - {end}</code>\n\n🛡️ <i>Cloud Media Management</i>",
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
        "ultra_tools_main": "💎 <b>ULTRA PRO Elite Tools — {group_name}</b>\n\nDirect panel control over your highest-tier modules.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_ultra_tools": "💎 ULTRA Elite Tools",
        "ultra_lock_generic": "🔒 <i>This module is available exclusively at the ULTRA PRO tier.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "panic_menu": "🚨 <b>Panic Button — Raid Lockdown</b>\n\n• <b>Status:</b> {status_str}",
        "panic_confirm": "⚠️ <b>Confirm Emergency Lockdown?</b>",
        "panic_activated": "🚨 <b>RAID LOCKDOWN ACTIVE</b>",
        "panic_deactivated": "🟢 <b>Lockdown lifted.</b>",
        "btn_panic_activate": "🚨 Activate Lockdown",
        "btn_panic_confirm": "✅ Confirm Lockdown",
        "btn_panic_deactivate": "🟢 Lift Lockdown",
        "shield_menu": "🎥 <b>Screen-Share Shield</b>\n\n• <b>Status:</b> {status_str}",
        "shield_updated_1": "🎥 Screen-Share Shield activated 🟢",
        "shield_updated_0": "🎥 Screen-Share Shield disabled 🔴",
        "btn_shield_1": "🟢 Activate Shield",
        "btn_shield_0": "🔴 Disable Shield",
        "podcast_menu": "🎙️ <b>Podcast Mode & Audio Ducking</b>\n\n• <b>Status:</b> {status_str}\n• <b>Level:</b> <code>{duck_level}%</code>",
        "podcast_updated_1": "🎙️ Podcast Mode activated 🟢",
        "podcast_updated_0": "🎙️ Podcast Mode disabled 🔴",
        "btn_podcast_1": "🟢 Activate Podcast Mode",
        "btn_podcast_0": "🔴 Disable",
        "btn_duck_level": "🎚️ Ducking Level: {duck_level}%",
        "duck_custom_prompt": "🎚️ <b>Custom Ducking Level (1-90):</b>",
        "duck_updated": "🎚️ <b>Ducking level updated to {duck_level}%!</b>",
        "duck_err": "⚠️ Enter a number between 1 and 90.",
        "speakers_menu": "🌟 <b>Paid Speakers Queue (/speakers)</b>\n\n• <b>Status:</b> {status_str}\n• <b>Rate:</b> <code>{price} Stars</code>\n• <b>In Queue:</b> <code>{queue_count}</code>",
        "speakers_updated_1": "🌟 Speakers Queue activated 🟢",
        "speakers_updated_0": "🌟 Speakers Queue disabled 🔴",
        "btn_speakers_1": "🟢 Activate Queue",
        "btn_speakers_0": "🔴 Disable Queue",
        "btn_speakers_price": "⭐ Priority Rate: {price} Stars",
        "btn_speakers_clear": "🧹 Clear Queue",
        "speakers_price_prompt": "⭐ <b>Custom Priority Rate (Stars):</b>",
        "speakers_price_updated": "⭐ <b>Priority Rate Updated to {price} Stars!</b>",
        "speakers_price_err": "⚠️ Enter a positive integer.",
        "speakers_cleared": "🧹 Speakers queue cleared 🟢",
        "tips_main": "⭐ <b>Telegram Stars Tips & Donations</b>\n\n• <b>Status:</b> {st_badge}\n• <b>Amount:</b> <code>{amount} Stars</code>\n• <b>Target:</b> <code>{target}</code>",
        "btn_tips": "⭐ Stars Tips & Donations",
        "tips_updated": "✅ Tips settings updated successfully!",
        "tips_prompt_amount": "💰 <b>Suggested Tip Amount (Stars):</b>",
        "tips_prompt_target": "📢 <b>Destination Channel (@username or ID):</b>",
        "tips_amount_err": "⚠️ Enter a positive integer.",
        "tips_target_err": "⚠️ Invalid target channel.",
        "sentinel_payload_main": "💎 <b>Sentinel Multimedia Payload (ULTRA PRO)</b>\n\n• <b>Status:</b> {st_badge}\n• <b>Text:</b> {has_text}\n• <b>Media:</b> {has_media}\n• <b>Auto-Delete:</b> <code>{autodel}</code>",
        "btn_sentinel_payload": "💎 Multimedia Payload",
        "sentinel_payload_prompt_text": "✍️ <b>Custom Payload Text (HTML supported):</b>",
        "sentinel_payload_prompt_media": "🖼️ <b>Upload Media Asset (Photo, GIF, or Video):</b>",
        "sentinel_payload_prompt_del": "⏱️ <b>Auto-Delete Timeout in seconds (0 to keep):</b>",
        "sentinel_payload_saved": "✅ <b>Payload asset updated successfully!</b>",
        "sentinel_payload_err": "⚠️ Invalid input for payload asset.",
        "btn_night_mode": "🌙 Autonomous Night Mode",
        "night_main": (
            "🏴‍☠️ <b>Autonomous Night Mode (Phase 4)</b>\n\n"
            "Automates perimeter shielding during low-supervision hours:\n\n"
            "• <b>Status:</b> {st_badge}\n"
            "• <b>Schedule:</b> <code>{start} - {end}</code>\n"
            "• <b>Action:</b> <code>{action}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "night_prompt": "⏰ <b>Night Mode Schedule</b>\n\nSend the start and end interval in 24h format (example: <code>22:00-06:00</code>):\n\n🛡️ <i>Cloud Media Management</i>",
        "night_updated": "✅ <b>Night Mode schedule updated successfully!</b>\n\n🛡️ <i>Cloud Media Management</i>",
        "night_err": "⚠️ Invalid format. Use HH:MM-HH:MM (Example: <code>22:00-06:00</code>).\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_night_on": "🟢 Enable Night Mode",
        "btn_night_off": "🔴 Disable Night Mode",
        "btn_night_mod": "⏰ Modify Schedule (HH:MM-HH:MM)"
    },
    "es": {
        "owner_only_alert": "⛔ Acceso Denegado: Esta consola táctica está reservada única y exclusivamente para el Dueño de la comunidad o canal.",
        "welcome": (
            "🏴‍☠️ <b>Bienvenido al Círculo Interno, {name}.</b>\n\n"
            "Soy <b>The Bunker Bot</b>, el núcleo arquitectónico de seguridad diseñado por <b>Master Tom</b>. Dentro de este dominio, posees autoridad absoluta para forjar el orden a partir del caos.\n\n"
            "Despliégame en tus grupos para defensa perimetral de élite, o en tus canales para automatizar suscripciones de pago y moderar transmisiones en vivo.\n\n"
            "Selecciona una opción abajo para auditar los módulos tácticos o acceder al Command Center.\n\n"
            "🛡️ <i>Desarrollado y respaldado por <b>Cloud Media Management</b>.</i>"
        ),
        "btn_add_group": "➕ Añadir a un Grupo",
        "btn_add_channel": "📢 Añadir a un Canal",
        "btn_settings": "⚙️ Configuración de Grupos",
        "btn_chsettings": "📡 Configuración de Canales",
        "btn_saas": "⚡ Command Center",
        "btn_id": "🛠️ Mi ID y Estado",
        "btn_support": "🆘 Soporte",
        "btn_info": "ℹ️ Información",
        "btn_how_works": "📖 ¿Cómo funciona el Búnker?",
        "settings_main": (
            "🛡️ <b>Centro de Mando de Comunidades (Grupos)</b>\n\n"
            "Toma el control perimetral total de tu comunidad:\n\n"
            "• 🤖 Aduana Captcha alfanumérica.\n"
            "• 🔒 Cerraduras de contenido y filtros Anti-Spam.\n"
            "• 🎙️ Centinela Dedicado y atenuación acústica en videollamadas.\n"
            "• 💰 Monetización con Telegram Stars y etiquetas VIP personalizadas.\n\n"
            "<i>Selecciona abajo el grupo que deseas auditar y blindar:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "chsettings_main": (
            "📡 <b>Estudio de Lives & Membresías (Canales)</b>\n\n"
            "Consola de transmisión en vivo y gestión de suscriptores para canales:\n\n"
            "• 🎙️ <b>Centinela en Vivo:</b> Apertura de micrófono al levantar la mano solo a usuarios verificados.\n"
            "• 💎 <b>Aduana de Suscripciones:</b> Enlaces criptográficos de un solo uso que se queman al entrar.\n"
            "• ⏳ <b>Auditoría Recurrente:</b> Alertas previas y expulsión automática de miembros morosos.\n"
            "• ⭐ <b>Propinas en Directo:</b> Monetización transparente con Telegram Stars.\n\n"
            "<i>Selecciona abajo el canal que deseas gestionar:</i>\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "support_main": (
            "🆘 <b>Soporte Táctico Oficial</b>\n\n"
            "Para asistencia directa, pases élite o arquitecturas personalizadas, contacta con nuestro Arquitecto Jefe:\n\n"
            "👤 <b>Contacto:</b> @therealonetom\n\n"
            "© <i>Cloud Media Management</i>"
        ),
        "group_panel_title": "🛡️ <b>Matriz de Seguridad:</b> {group_name}\n\nSelecciona un módulo para alterar los parámetros de la comunidad.",
        "channel_panel_title": "📡 <b>Estudio de Transmisión:</b> {channel_name}\n\nSelecciona un módulo para configurar transmisiones en vivo, suscripciones o automatizaciones.",
        "pay_pro_title": (
            "⭐ <b>Suscripción Plan PRO — {group_name} (300 Stars)</b>\n\n"
            "• ⚡ <b>Comandos de Bot Ilimitados.</b>\n"
            "• 🗑️ <b>Purga Automatizada de mensajes de servicio.</b>\n"
            "• 🤖 <b>Aduana Captcha Pro Avanzada.</b>\n"
            "• 🛡️ <b>Anti-Spam Granular Total.</b>\n\n"
            "<i>Selecciona tu pasarela preferida:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "pay_ultra_title": (
            "💎 <b>Suscripción ULTRA PRO — {group_name} (600 Stars)</b>\n\n"
            "• 🌟 <b>Todas las ventajas del Plan PRO incluidas.</b>\n"
            "• 🧬 <b>Arquitectura Bot Clone:</b> Tu réplica bajo tu propio token.\n"
            "• 🎙️ <b>Centinela de Voz Dedicado:</b> Moderación acústica en videollamadas vía teléfono.\n"
            "• 🏷️ <b>Etiquetas Nativas VIP y Radar AutoLower al 2%.</b>\n"
            "• 💰 <b>Monetización Stars directa a tu balance.</b>\n"
            "• 📢 <b>Motor de Membresías en Canales:</b> Enlaces de 1 solo uso y expulsión automática.\n\n"
            "<i>Selecciona tu pasarela preferida:</i>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "btn_back": "🔙 Volver al Menú Principal",
        "btn_back_settings": "🔙 Volver a Grupos",
        "btn_back_chsettings": "🔙 Volver a Canales",
        "btn_back_group": "🔙 Panel del Grupo",
        "btn_back_channel": "🔙 Panel del Canal",
        "btn_back_captcha": "🔙 Volver a Captcha",
        "btn_back_antispam": "🔙 Volver a Anti-Spam",
        "btn_back_antiflood": "🔙 Volver a Anti-Flood",
        "mod_main": (
            "🛡️ <b>Matriz de Moderación Táctica</b>\n\n"
            "Consola remota para <b>{group_name}</b>:\n\n"
            "• 🚫 <b>Baneo (/ban) y Expulsión (/kick)</b>\n"
            "• 🔇 <b>Silencio (/mute) y Restaurar (/unmute)</b>\n"
            "• ⚪ <b>Lista Blanca (Whitelist) y Lista Negra (Blacklist)</b>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "eco_main": (
            "📡 <b>Radar y Control del Ecosistema</b>\n\n"
            "• 📡 <b>Radar Ecosistema y /cams</b>\n"
            "• ⚙️ <b>/autolower y Programador VC</b>\n"
            "• 🎙️ <b>/mic_vip y Propinas Stars</b>\n\n"
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
            "• <b>Estado:</b> {status_text}\n"
            "• <b>Modo Alfanumérico:</b> {mode_text}\n"
            "• <b>Tiempo Límite:</b> {time_text}s\n"
            "• <b>Castigo:</b> {action_text}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "captcha_action_title": "⚖️ <b>Configuración de Castigo</b>\n\n• <b>Castigo Activo:</b> {mode_name} 🟢\n\n🛡️ <i>Cloud Media Management</i>",
        "antispam_main_title": (
            "✉️ <b>Matriz Anti-Spam Granular</b>\n\n"
            "• 📘 <b>Enlaces Telegram:</b> {st_tg}\n"
            "• 📥 <b>Escudo Reenvíos:</b> {st_fwd}\n"
            "• 💭 <b>Filtro Citas:</b> {st_q}\n"
            "• 🔗 <b>Enlaces Internet:</b> {st_web}\n"
            "• 🗑️ <b>Borrado Spam:</b> {st_del}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "locks_main_title": (
            "🔒 <b>Panel de Cerraduras (Locks)</b>\n\n"
            "• <b>Multimedia:</b> {media}\n"
            "• <b>Stickers/GIFs:</b> {stickers}\n"
            "• <b>Enlaces:</b> {links}\n"
            "• <b>Comandos:</b> {commands}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "warns_main_title": (
            "⚠️ <b>Matriz de Advertencias (Warns)</b>\n\n"
            "• <b>Límite Strikes:</b> {limit}\n"
            "• <b>Castigo:</b> {action}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "delmsgs_main_title": (
            "🗑️ <b>Centro Avanzado de Purga de Mensajes</b>\n\n"
            "• <b>Nivel del Plan:</b> {tier_display}\n"
            "• <b>Privilegio / Cuota:</b> {quota_desc}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "antiflood_main_title": "🗣️ <b>Escudo Anti-Flood</b>\n\n<b>Umbral:</b> {msgs} msgs en {time}s.\n<b>Acción:</b> {action}\n<b>Borrar:</b> {delete_st}",
        "forwards_panel": "🌊 <b>Configuración del Escudo de Reenvíos</b>",
        "clone_main_title": (
            "🧬 <b>Arquitectura de Clonación & Centinela Dedicado</b>\n\n"
            "• <b>Licencia:</b> {tier}\n"
            "• <b>Bot Clone:</b> {status}\n"
            "• <b>Centinela Dedicado:</b> {sentinel_status}\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "botfather_guide": "🔑 <b>Conectar Bot Clon:</b> Envía <code>/newbot</code> a @BotFather y pega el token aquí.",
        "sentinel_phone_guide": "🎙️ <b>Conectar Centinela:</b> Envía tu número telefónico con código internacional (ej. <code>+573001234567</code>).",
        "captcha_saved": "✅ <b>¡Mensaje de captcha guardado!</b>\n\n<i>(Este aviso se auto-eliminará en 60s)</i>",
        "token_verifying": "🔄 <b>Verificando token con Telegram...</b>",
        "token_success": "✅ <b>¡Token recibido y verificado correctamente!</b>",
        "token_error": "❌ <b>Token inválido.</b>",
        "phone_requesting": "🔄 <b>Solicitando código oficial de Telegram...</b>",
        "phone_sent": "📩 <b>¡Código Oficial Enviado!</b>\n\nEscribe el código numérico recibido a continuación:",
        "phone_error": "❌ <b>Error al enviar código:</b> {reason}",
        "code_verifying": "🔄 <b>Verificando código y autorizando Centinela...</b>",
        "sentinel_success": "💎 <b>¡Centinela Propio Conectado con Éxito!</b>",
        "sentinel_error": "❌ <b>Error al inicializar sesión.</b>",
        "twofa_required": "🔐 <b>Verificación en Dos Pasos (2FA) Requerida</b>\n\nIngresa tu contraseña:",
        "code_invalid": "❌ <b>Código inválido o expirado.</b>",
        "twofa_verifying": "🔄 <b>Validando contraseña 2FA...</b>",
        "twofa_invalid": "❌ <b>Contraseña 2FA incorrecta.</b>",
        "sched_updated": "✅ <b>¡Horario Actualizado!</b> (Inicio: {start} | Cierre: {end})",
        "sched_err": "⚠️ Formato incorrecto. Usa HH:MM-HH:MM (ej. <code>20:00-23:30</code>).",
        "wl_success": "✅ <b>¡Usuario registrado en Whitelist!</b> (ID: <code>{target_id}</code>)",
        "id_err": "⚠️ <b>ID inválido.</b>",
        "bl_success": "✅ <b>¡Término prohibido registrado en Blacklist!</b> (<code>{word}</code>)",
        "dir_ban": "🚫 <b>¡Baneo ejecutado remotamente!</b> (ID: <code>{target_id}</code>)",
        "dir_kick": "👢 <b>¡Expulsión ejecutada remotamente!</b> (ID: <code>{target_id}</code>)",
        "dir_mute": "🔇 <b>¡Silencio ejecutado remotamente!</b> (ID: <code>{target_id}</code>)",
        "dir_unmute": "🔊 <b>¡Permisos restaurados!</b> (ID: <code>{target_id}</code>)",
        "dir_err": "❌ <b>Error al ejecutar directiva:</b> {ex}",
        "mic_updated": "⭐ <b>¡Tarifa VIP de Micrófono actualizada a {price_val} Stars!</b>",
        "mic_err": "⚠️ Ingresa un número entero positivo.",
        "tag_updated": "🏷️ <b>¡Etiqueta VIP actualizada a: {text_input}!</b>",
        "tag_err": "⚠️ La etiqueta debe tener entre 1 y 16 caracteres.",
        "mod_id_err": "⚠️ <b>Objetivo inválido.</b>",
        "btn_cancel_ret": "❌ Cancelar y Volver",
        "btn_retry": "🔄 Reintentar",
        "op_canceled": "Operación cancelada y memoria liberada.",
        "sentinel_disc": "🛑 Centinela propio desconectado con éxito.",
        "clone_disc": "🛑 Bot Clon desconectado con éxito.",
        "tag_pro_req": "💎 Requiere nivel ULTRA PRO.",
        "al_updated_1": "AutoLower actualizado 🟢",
        "al_updated_0": "AutoLower desactivado 🔴",
        "mic_alert_set": "Tarifa configurada a {price_int} Stars ⭐",
        "wl_menu": "⚪ <b>Directiva: Lista Blanca Táctica (Whitelist)</b>\n\nIdentidades con <b>Inmunidad Absoluta</b>.\n\n🛡️ <i>Cloud Media Management</i>",
        "bl_menu": "⚫ <b>Directiva: Lista Negra Global (Blacklist)</b>\n\nTérminos con purga automática y advertencias.\n\n🛡️ <i>Cloud Media Management</i>",
        "cams_menu": "📹 <b>Supervisión de Cámaras y Transmisiones</b>\n\nAuditoría en vivo de estabilidad activa.\n\n🛡️ <i>Cloud Media Management</i>",
        "al_menu": "⚙️ <b>Control Remoto: AutoLower de Videollamada</b>\n\n• <b>Estado:</b> {status_str}\n\n🛡️ <i>Cloud Media Management</i>",
        "mic_menu": "🎙️ <b>Pase VIP de Micrófono (Stars)</b>\n\n• <b>Tarifa:</b> <code>{curr_price} Stars</code>\n• <b>Etiqueta:</b> <code>{curr_tag}</code>\n\n🛡️ <i>Cloud Media Management</i>",
        "tag_menu_prompt": "🏷️ <b>Editor de Etiqueta VIP Nativa</b> (Máx 16 caracteres):",
        "mod_ask_time": "⚡ <b>Directiva: /{sub_cmd}</b>\n\nSelecciona la duración:",
        "mod_ask_target": "🎯 <b>Objetivo — /{sub_cmd_upper}</b>\n\nEnvía el @usuario o ID:",
        "reg_ask": "📝 <b>Registro en Base de Datos</b>\n\nEnvía {target_name}:",
        "reg_ask_wl": "la ID del usuario para Whitelist",
        "reg_ask_bl": "el término prohibido para Blacklist",
        "vcsched_prompt": "⏰ <b>Configuración Horario VC</b> (ej. <code>20:00-23:30</code>):",
        "mic_custom_prompt": "⭐ <b>Tarifa Personalizada de Stars:</b>",
        "vcsched_main": "🗓️ <b>Programador de Videochats (ULTRA PRO)</b>\n\n• <b>Estado:</b> {st_badge}\n• <b>Horario:</b> <code>{start} - {end}</code>\n\n🛡️ <i>Cloud Media Management</i>",
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
        "ultra_tools_main": "💎 <b>Herramientas de Élite ULTRA PRO — {group_name}</b>\n\nControl perimetral avanzado y transmisión.\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_ultra_tools": "💎 Herramientas ULTRA",
        "ultra_lock_generic": "🔒 <i>Este módulo está disponible exclusivamente en el nivel ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>",
        "panic_menu": "🚨 <b>Botón de Pánico — Bloqueo de Emergencia</b>\n\n• <b>Estado:</b> {status_str}",
        "panic_confirm": "⚠️ <b>¿Confirmar Bloqueo de Emergencia?</b>",
        "panic_activated": "🚨 <b>BLOQUEO DE EMERGENCIA ACTIVO</b>",
        "panic_deactivated": "🟢 <b>Bloqueo levantado.</b>",
        "btn_panic_activate": "🚨 Activar Bloqueo",
        "btn_panic_confirm": "✅ Confirmar Bloqueo",
        "btn_panic_deactivate": "🟢 Levantar Bloqueo",
        "shield_menu": "🎥 <b>Escudo Antinota (Pantalla Compartida)</b>\n\n• <b>Estado:</b> {status_str}",
        "shield_updated_1": "🎥 Escudo Antinota activado 🟢",
        "shield_updated_0": "🎥 Escudo Antinota desactivado 🔴",
        "btn_shield_1": "🟢 Activar Escudo",
        "btn_shield_0": "🔴 Desactivar Escudo",
        "podcast_menu": "🎙️ <b>Modo Podcast & Audio Ducking</b>\n\n• <b>Estado:</b> {status_str}\n• <b>Ducking:</b> <code>{duck_level}%</code>",
        "podcast_updated_1": "🎙️ Modo Podcast activado 🟢",
        "podcast_updated_0": "🎙️ Modo Podcast desactivado 🔴",
        "btn_podcast_1": "🟢 Activar Modo Podcast",
        "btn_podcast_0": "🔴 Desactivar",
        "btn_duck_level": "🎚️ Nivel de Ducking: {duck_level}%",
        "duck_custom_prompt": "🎚️ <b>Nivel de Ducking Personalizado (1-90):</b>",
        "duck_updated": "🎚️ <b>¡Ducking actualizado al {duck_level}%!</b>",
        "duck_err": "⚠️ Ingresa un número entero entre 1 y 90.",
        "speakers_menu": "🌟 <b>Cola de Speakers Pagada (/speakers)</b>\n\n• <b>Estado:</b> {status_str}\n• <b>Tarifa:</b> <code>{price} Stars</code>\n• <b>En Cola:</b> <code>{queue_count}</code>",
        "speakers_updated_1": "🌟 Cola de Speakers activada 🟢",
        "speakers_updated_0": "🌟 Cola de Speakers desactivada 🔴",
        "btn_speakers_1": "🟢 Activar Cola",
        "btn_speakers_0": "🔴 Desactivar Cola",
        "btn_speakers_price": "⭐ Tarifa Prioridad: {price} Stars",
        "btn_speakers_clear": "🧹 Vaciar Cola",
        "speakers_price_prompt": "⭐ <b>Tarifa de Prioridad Personalizada (Stars):</b>",
        "speakers_price_updated": "⭐ <b>¡Tarifa de Prioridad Actualizada a {price} Stars!</b>",
        "speakers_price_err": "⚠️ Ingresa un entero positivo de Stars.",
        "speakers_cleared": "🧹 Cola de speakers vaciada 🟢",
        "tips_main": "⭐ <b>Propinas y Donaciones con Telegram Stars (XTR)</b>\n\n• <b>Estado:</b> {st_badge}\n• <b>Monto:</b> <code>{amount} Stars</code>\n• <b>Canal:</b> <code>{target}</code>",
        "btn_tips": "⭐ Propinas Stars",
        "tips_updated": "✅ ¡Ajustes de propinas actualizados con éxito!",
        "tips_prompt_amount": "💰 <b>Monto Sugerido de Propinas (Stars):</b>",
        "tips_prompt_target": "📢 <b>Destination Channel (@username or ID):</b>",
        "tips_amount_err": "⚠️ Ingresa un número entero positivo.",
        "tips_target_err": "⚠️ Canal objetivo no válido.",
        "sentinel_payload_main": "💎 <b>Payload Multimedia del Centinela (ULTRA PRO)</b>\n\n• <b>Estado:</b> {st_badge}\n• <b>Texto:</b> {has_text}\n• <b>Multimedia:</b> {has_media}\n• <b>Auto-Borrado:</b> <code>{autodel}</code>",
        "btn_sentinel_payload": "💎 Payload Multimedia",
        "sentinel_payload_prompt_text": "✍️ <b>Texto Personalizado del Payload (HTML soportado):</b>",
        "sentinel_payload_prompt_media": "🖼️ <b>Carga de Multimedia del Payload (Foto, GIF o Video):</b>",
        "sentinel_payload_prompt_del": "⏱️ <b>Tiempo de Auto-Borrado en segundos (0 para mantener):</b>",
        "sentinel_payload_saved": "✅ <b>¡Activo de payload guardado correctamente!</b>",
        "sentinel_payload_err": "⚠️ Entrada no válida para el activo multimedia del payload.",
        "btn_night_mode": "🌙 Modo Nocturno Autónomo",
        "night_main": (
            "🌙 <b>Modo Nocturno Autónomo (Fase 4)</b>\n\n"
            "Automatiza el blindaje perimetral de tu comunidad durante las horas de menor supervisión:\n\n"
            "• <b>Estado:</b> {st_badge}\n"
            "• <b>Horario:</b> <code>{start} - {end}</code>\n"
            "• <b>Acción:</b> <code>{action}</code>\n\n"
            "🛡️ <i>Cloud Media Management</i>"
        ),
        "night_prompt": "⏰ <b>Configuración de Horario Nocturno</b>\n\nEnvía en este chat el intervalo de inicio y cierre en formato 24h (ejemplo: <code>22:00-06:00</code>):\n\n🛡️ <i>Cloud Media Management</i>",
        "night_updated": "✅ <b>¡Horario del Modo Nocturno actualizado con éxito!</b>\n\n🛡️ <i>Cloud Media Management</i>",
        "night_err": "⚠️ Formato incorrecto. Usa HH:MM-HH:MM (Ejemplo: <code>22:00-06:00</code>).\n\n🛡️ <i>Cloud Media Management</i>",
        "btn_night_on": "🟢 Activar Modo Nocturno",
        "btn_night_off": "🔴 Desactivar Modo Nocturno",
        "btn_night_mod": "⏰ Modificar Horario (HH:MM-HH:MM)"
    }
}
async def get_active_user_groups(bot: Bot, user_id: int) -> list:
    # Function implementation goes here
    pass
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
        except TelegramForbiddenError:
            return None
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "chat not found" in msg or "kicked" in msg or "not a member" in msg:
                return None
            return (g_id, g_name)
        except Exception:
            return (g_id, g_name)

        if bot_member.status not in ("administrator", "creator"):
            return None

        try:
            user_member = await bot.get_chat_member(chat_id=g_id, user_id=user_id)
        except Exception:
            return (g_id, g_name)

        return (g_id, g_name) if user_member.status == "creator" else None

    results = await asyncio.gather(*(check_ownership(g_id, g_name) for g_id, g_name in raw_groups))
    return [res for res in results if res is not None]


async def get_active_user_channels(bot: Bot, user_id: int) -> list:
    raw_channels = await get_user_channels(user_id)
    if not raw_channels:
        return []
    if is_super_admin(user_id):
        return raw_channels
    bot_info = await bot.get_me()
    bot_id = bot_info.id

    async def check_ch_ownership(c_id, c_name):
        try:
            bot_member = await bot.get_chat_member(chat_id=c_id, user_id=bot_id)
        except TelegramForbiddenError:
            return None
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "chat not found" in msg or "kicked" in msg or "not a member" in msg:
                return None
            return (c_id, c_name)
        except Exception:
            return (c_id, c_name)

        if bot_member.status not in ("administrator", "creator"):
            return None

        try:
            user_member = await bot.get_chat_member(chat_id=c_id, user_id=user_id)
        except Exception:
            return (c_id, c_name)

        return (c_id, c_name) if user_member.status == "creator" else None

    results = await asyncio.gather(*(check_ch_ownership(c_id, c_name) for c_id, c_name in raw_channels))
    return [res for res in results if res is not None]


async def is_registered_owner_db(user_id: int, group_id: int) -> bool:
    def _sync():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM user_groups WHERE user_id = ? AND group_id = ? LIMIT 1",
                (user_id, group_id)
            )
            if cursor.fetchone():
                return True
            cursor.execute(
                "SELECT 1 FROM bot_clones WHERE user_id = ? AND group_id = ? AND status != 'revoked' LIMIT 1",
                (user_id, group_id)
            )
            return cursor.fetchone() is not None
    try:
        return await asyncio.to_thread(_sync)
    except Exception as ex:
        logging.error(f"❌ [Aduana] Fallo al consultar propiedad en DB para user={user_id} group={group_id}: {ex}")
        return False


async def is_legitimate_owner(bot: Bot, user_id: int, group_id: int) -> bool:
    if is_super_admin(user_id):
        return True
    if await is_registered_owner_db(user_id, group_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=user_id)
        if member.status == "creator":
            return True
    except Exception:
        pass
    return False


async def verify_admin_privileges(callback: CallbackQuery, bot: Bot, group_id: int) -> bool:
    lang = "es" if callback.from_user.language_code and callback.from_user.language_code.startswith("es") else "en"
    t = TEXTS[lang]
    if await is_legitimate_owner(bot, callback.from_user.id, group_id):
        return True
    await callback.answer(t["owner_only_alert"], show_alert=True)
    return False


async def verify_admin_privileges_msg(message: Message, bot: Bot, group_id: int) -> bool:
    lang = "es" if message.from_user.language_code and message.from_user.language_code.startswith("es") else "en"
    t = TEXTS[lang]
    if await is_legitimate_owner(bot, message.from_user.id, group_id):
        return True
    await message.answer(t["owner_only_alert"])
    return False


async def resolve_chat_kind(bot: Bot, chat_id: int) -> str:
    try:
        chat_obj = await bot.get_chat(chat_id)
        return "c" if chat_obj.type == "channel" else "g"
    except Exception:
        return "g"


def get_main_keyboard(bot_username: str, lang: str, is_clone: bool = False):
    t = TEXTS.get(lang, TEXTS["es"])
    add_group_url = f"https://t.me/{bot_username}?startgroup=true&admin=restrict_members+ban_users+delete_messages+pin_messages+manage_video_chats+promote_members"
    add_channel_url = f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages+delete_messages+manage_video_chats+invite_users"

    rows = [
        [
            InlineKeyboardButton(text=t["btn_add_group"], url=add_group_url),
            InlineKeyboardButton(text=t["btn_add_channel"], url=add_channel_url)
        ],
        [
            InlineKeyboardButton(text=t["btn_settings"], callback_data=f"menu_settings_{lang}"),
            InlineKeyboardButton(text=t["btn_chsettings"], callback_data=f"menu_chsettings_{lang}")
        ]
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


def get_channels_keyboard(channels: list, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    if not channels:
        no_ch_text = "⚠️ No hay canales activos vinculados" if lang == "es" else "⚠️ No active channels linked"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=no_ch_text, callback_data="noop")],
            [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
        ])
    kb = [[InlineKeyboardButton(text=f"📢 {c_name}", callback_data=f"cpanel_{c_id}_{lang}")] for c_id, c_name in channels]
    kb.append([InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def get_channel_panel_keyboard(channel_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    toggle_lang = "en" if lang == "es" else "es"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 ULTRA PRO", callback_data=f"pay_ultra_{channel_id}_{lang}")],
        [
            InlineKeyboardButton(text="💎 " + ("Planes de Membresía" if lang == "es" else "Membership Plans"), callback_data=f"chplans_menu_{channel_id}_{lang}"),
            InlineKeyboardButton(text="🎙️ " + ("Moderación de Live" if lang == "es" else "Live Moderation"), callback_data=f"menu_ultra_{channel_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="⭐ " + ("Propinas Stars" if lang == "es" else "Stars Tips"), callback_data=f"tips_menu_{channel_id}_{lang}"),
            InlineKeyboardButton(text="💎 " + ("Payload Multimedia" if lang == "es" else "Media Payload"), callback_data=f"payload_menu_{channel_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text="🧬 " + ("Clon & Centinela" if lang == "es" else "Clone & Sentinel"), callback_data=f"gset_clone_{channel_id}_{lang}")
        ],
        [
            InlineKeyboardButton(text=f"🌐 {'English' if lang == 'es' else 'Español'}", callback_data=f"langcpanel_{channel_id}_{toggle_lang}"),
            InlineKeyboardButton(text=t["btn_back_chsettings"], callback_data=f"menu_chsettings_{lang}")
        ]
    ])


def get_group_panel_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    toggle_lang = "en" if lang == "es" else "es"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ PRO", callback_data=f"pay_pro_{group_id}_{lang}"), InlineKeyboardButton(text="💎 ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_mod"], callback_data=f"menu_mod_{group_id}_{lang}"), InlineKeyboardButton(text=t["btn_eco"], callback_data=f"menu_eco_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🧹 Ghost Purge 🟢", callback_data=f"gset_purge_{group_id}_{lang}")],  # 🟩 Botón integrado con acento verde
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


def get_ultra_tools_keyboard(group_id: int, lang: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 " + ("Botón de Pánico" if lang == "es" else "Panic Button"), callback_data=f"panic_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎥 " + ("Escudo Antinota" if lang == "es" else "Screen-Share Shield"), callback_data=f"shield_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🎙️ " + ("Modo Podcast" if lang == "es" else "Podcast Mode"), callback_data=f"podcast_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="🌟 " + ("Gestión de Speakers" if lang == "es" else "Speakers Management"), callback_data=f"speakers_menu_{group_id}_{lang}")],
        [InlineKeyboardButton(text="💎 " + ("Payload Multimedia" if lang == "es" else "Multimedia Payload"), callback_data=f"payload_menu_{group_id}_{lang}")],
        [back_btn]
    ])


def build_ultra_lock_view(group_id: int, lang: str, feature_title: str):
    t = TEXTS.get(lang, TEXTS["es"])
    lock_text = f"{feature_title}\n\n{t['ultra_lock_generic']}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Upgrade to ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")]
    ])
    return lock_text, keyboard


def get_panic_keyboard(group_id: int, lang: str, status: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    action_btn = (
        InlineKeyboardButton(text=t["btn_panic_deactivate"], callback_data=f"panic_deactivate_{group_id}_{lang}")
        if status == 1 else
        InlineKeyboardButton(text=t["btn_panic_activate"], callback_data=f"panic_confirm_{group_id}_{lang}")
    )
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [action_btn],
        [back_btn]
    ])


def get_night_keyboard(group_id: int, lang: str, status: int):
    t = TEXTS.get(lang, TEXTS["es"])
    toggle_btn = (
        InlineKeyboardButton(text=t["btn_night_off"], callback_data=f"night_toggle_0_{group_id}_{lang}")
        if status == 1 else
        InlineKeyboardButton(text=t["btn_night_on"], callback_data=f"night_toggle_1_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle_btn],
        [InlineKeyboardButton(text=t["btn_night_mod"], callback_data=f"night_prompt_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
    ])


def get_panic_confirm_keyboard(group_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_panic_confirm"], callback_data=f"panic_activate_{group_id}_{lang}")],
        [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"panic_menu_{group_id}_{lang}")]
    ])


def get_shield_keyboard(group_id: int, lang: str, status: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_shield_1"], callback_data=f"shield_toggle_1_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_shield_0"], callback_data=f"shield_toggle_0_{group_id}_{lang}")
        ],
        [back_btn]
    ])


def get_podcast_keyboard(group_id: int, lang: str, status: int, duck_level: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")
    )
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
        [back_btn]
    ])


def get_speakers_keyboard(group_id: int, lang: str, status: int, price: int, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")
    )
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
        [back_btn]
    ])


def get_sentinel_payload_keyboard(group_id: int, lang: str, cfg: dict, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    st = cfg.get("enabled", 0)
    has_text = "🟢" if cfg.get("text") else "🔴"
    has_media = f"🟢 ({cfg.get('media_type')})" if cfg.get("media_id") else "🔴"
    autodel = f"{cfg.get('auto_delete_after')}s" if cfg.get("auto_delete_after") else ("Desactivado" if lang == "es" else "Off")
    st_label = f"💎 {'Payload: 🟢' if st == 1 else 'Payload: 🔴'}"
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"menu_ultra_{group_id}_{lang}")
    )

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=st_label, callback_data=f"payload_toggle_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'✍️ Texto Personalizado' if lang == 'es' else '✍️ Custom Text'} {has_text}", callback_data=f"payload_text_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'🖼️ Multimedia (Foto/Anim)' if lang == 'es' else '🖼️ Media (Photo/Anim)'} {has_media}", callback_data=f"payload_media_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'⏱️ Auto-Borrado' if lang == 'es' else '⏱️ Auto-Delete'}: {autodel}", callback_data=f"payload_autodel_{group_id}_{lang}")],
        [back_btn]
    ])


def get_tips_keyboard(group_id: int, lang: str, cfg: dict, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    st = cfg.get("enabled", 0)
    amount = cfg.get("amount", 10)
    target = cfg.get("target_channel") or ("No asignado" if lang == "es" else "Not set")

    st_label = f"⭐ {'Propinas: 🟢' if st == 1 else 'Propinas: 🔴'}"
    amt_label = f"💰 {amount} Stars"
    target_label = f"📢 {target[:15]}"

    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")
    )

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=st_label, callback_data=f"tips_toggle_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'Monto Sugerido' if lang == 'es' else 'Suggested'}: {amt_label}", callback_data=f"tips_setamount_{group_id}_{lang}")],
        [InlineKeyboardButton(text=f"{'Canal Destino' if lang == 'es' else 'Target'}: {target_label}", callback_data=f"tips_settarget_{group_id}_{lang}")],
        [back_btn]
    ])


def get_payment_keyboard(group_id: int, lang: str, tier_level: str = "pro", chat_type: str = "g"):
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

    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    keyboard_rows.append([back_btn])
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

    links_on = cfg.get("warn_links", 1) == 1
    blacklist_on = cfg.get("warn_blacklist", 1) == 1
    flood_on = cfg.get("warn_flood", 1) == 1

    links_dot = "🟢" if links_on else "🔴"
    blacklist_dot = "🟢" if blacklist_on else "🔴"
    flood_dot = "🟢" if flood_on else "🔴"

    links_lbl = f"🔗 {'Warn by Forbidden Links' if lang == 'en' else 'Aviso por Enlaces Prohibidos'} {links_dot}"
    blacklist_lbl = f"🚫 {'Warn by Blacklisted Words' if lang == 'en' else 'Aviso por Lista Negra'} {blacklist_dot}"
    flood_lbl = f"🌊 {'Warn by Anti-Flood' if lang == 'en' else 'Aviso por Anti-Flood'} {flood_dot}"

    limit_lbl = f"🔢 Strike Limit: {limit}" if lang == "en" else f"🔢 Límite: {limit} Faltas"
    action_lbl = f"⚖️ Punishment: {action}" if lang == "en" else f"⚖️ Castigo: {action}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=links_lbl, callback_data=f"warnset_toglink_{group_id}_{lang}")],
        [InlineKeyboardButton(text=blacklist_lbl, callback_data=f"warnset_togblack_{group_id}_{lang}")],
        [InlineKeyboardButton(text=flood_lbl, callback_data=f"warnset_togflood_{group_id}_{lang}")],
        [
            InlineKeyboardButton(text=limit_lbl, callback_data=f"warnset_limit_{group_id}_{lang}"),
            InlineKeyboardButton(text=action_lbl, callback_data=f"warnset_action_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


async def get_delmsgs_keyboard(group_id: int, user_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_effective_group_tier(group_id, user_id)
    cfg = await get_captcha_config(group_id)
    srv_mode = await get_service_msgs_mode(group_id)

    srv_del = "🟢" if cfg["service_del"] == 1 else "🔴"
    main_chat = "🟢" if await get_antispam_delete(group_id) == 1 else "🔴"
    block_cmds = "🟢" if await get_lock_status(group_id, "lock_commands") == 1 else "🔴"
    srv_mode_st = "🟢" if srv_mode == 1 else "🔴"

    if tier == "free":
        tier_text = "⭐ Tier: FREE (3 purges/day)" if lang == "en" else "⭐ Plan: BÁSICO (3 purgas/día)"
        pro_btn = "⭐ Mejorar a PRO" if lang == "es" else "⭐ Upgrade PRO"
        ultra_btn = "💎 Mejorar a ULTRA" if lang == "es" else "💎 Upgrade ULTRA"
        
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tier_text, callback_data=f"delmsgs_tier_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{'🗑️ Service Msgs' if lang == 'en' else '🗑️ Msgs de Servicio'} {srv_del}", callback_data=f"cap_set_srvdel_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{'🧹 Service Mode' if lang == 'en' else '🧹 Modo Servicio'} {srv_mode_st}", callback_data=f"delmsgs_srvmode_{group_id}_{lang}")],
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
        srv_mode_lbl = "🧹 Service Msgs Mode" if lang == "en" else "🧹 Modo Mensajes de Servicio"

        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tier_text, callback_data=f"delmsgs_tier_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{purge_lbl} {srv_del}", callback_data=f"cap_set_srvdel_{group_id}_{lang}")],
            [InlineKeyboardButton(text=f"{srv_mode_lbl} {srv_mode_st}", callback_data=f"delmsgs_srvmode_{group_id}_{lang}")],
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
        [
            InlineKeyboardButton(text=t["btn_tips"], callback_data=f"tips_menu_{group_id}_{lang}"),
            InlineKeyboardButton(text=t["btn_night_mode"], callback_data=f"night_menu_{group_id}_{lang}")
        ],
        [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")]
    ])


async def get_clone_keyboard(group_id: int, user_id: int, lang: str, chat_type: str = "g"):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_effective_group_tier(group_id, user_id)
    back_btn = (
        InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{group_id}_{lang}")
        if chat_type == "c" else
        InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")
    )
    if tier == "ultra_pro":
        clone_info = await get_bot_clone(user_id, group_id)
        has_clone = clone_info is not None and clone_info[2] == 'active' and bool(clone_info[0])
        session_info = await get_owner_session(user_id, group_id)
        has_sentinel = session_info is not None
        
        token_btn_text = "🔄 Actualizar Token @BotFather" if has_clone else "🔑 Conectar Token @BotFather"
        sentinel_btn_text = "🔄 Actualizar Centinela (Teléfono) 🟢" if has_sentinel else "🎙️ Conectar Centinela Propio (Teléfono) 🔴"

        kb = [
            [InlineKeyboardButton(text=token_btn_text, callback_data=f"clone_token_{group_id}_{lang}")],
            [InlineKeyboardButton(text=sentinel_btn_text, callback_data=f"clone_phone_{group_id}_{lang}")]
        ]
        if has_clone:
            kb.append([InlineKeyboardButton(text="🛑 Desconectar Bot Clon", callback_data=f"clone_discbot_{group_id}_{lang}")])
        if has_sentinel:
            kb.append([InlineKeyboardButton(text="🛑 Desconectar Centinela", callback_data=f"clone_discsentinel_{group_id}_{lang}")])

        kb.append([back_btn])
        return InlineKeyboardMarkup(inline_keyboard=kb)
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Desbloquear con ULTRA" if lang == "es" else "💎 Unlock with ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
            [back_btn]
        ])


# ==========================================
# 🧹 PANEL INTERACTIVO: GHOST PURGE DE ÉLITE
# ==========================================
async def get_purge_keyboard(group_id: int, user_id: int, lang: str):
    t = TEXTS.get(lang, TEXTS["es"])
    tier = await get_effective_group_tier(group_id, user_id)
    
    purge_cfg = await get_ghost_purge_config(group_id)
    action_mode = purge_cfg.get("action", "ban").upper()
    action_badge = f"⚡ Castigo: {action_mode} 🟢"
    
    sched_st = purge_cfg.get("schedule_status", 0)
    sched_badge = f"⏰ Programación: {'🟢 Activa' if sched_st == 1 else '🔴 Inactiva'}"
    
    kb_rows = [
        [InlineKeyboardButton(text="🚀 Ejecutar Escaneo y Purga Ahora", callback_data=f"purge_run_{group_id}_{lang}")],
        [InlineKeyboardButton(text=action_badge, callback_data=f"purge_togaction_{group_id}_{lang}")],
    ]
    
    if tier in ["pro", "ultra_pro"]:
        kb_rows.append([InlineKeyboardButton(text=sched_badge, callback_data=f"purge_sched_{group_id}_{lang}")])
    else:
        kb_rows.append([InlineKeyboardButton(text="🔒 Programación (Requiere PRO o ULTRA)", callback_data=f"pay_pro_{group_id}_{lang}")])
        
    kb_rows.append([InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"gpanel_{group_id}_{lang}")])
    
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


@router.callback_query(F.data.startswith("gset_purge_"))
async def cb_purge_menu(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
    group_id = int(data[3])
    lang = data[4] if data[4] in ["es", "en"] else "es"
    
    if not await verify_admin_privileges(callback, bot, group_id):
        return

    try:
        g_name = (await bot.get_chat(group_id)).title
    except Exception:
        g_name = "Comunidad"

    purge_cfg = await get_ghost_purge_config(group_id)
    
    text = (
        f"🧹 <b>Ghost Purge & Userbot Defense — {g_name}</b>\n\n"
        f"Sistema avanzado de barrido perimetral:\n"
        f"• 👻 <b>Detección:</b> Cuentas eliminadas (fantasmas) e IDs inválidos.\n"
        f"• ⚡ <b>Castigo Configurado:</b> <code>{purge_cfg.get('action', 'ban').upper()}</code>\n"
        f"• ⏳ <b>Límite Plan Free:</b> 1 escaneo cada 24 horas (Ilimitado en PRO/ULTRA).\n\n"
        f"<i>Selecciona una acción abajo:</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    ) if lang == "es" else (
        f"🧹 <b>Ghost Purge & Userbot Defense — {g_name}</b>\n\n"
        f"Advanced perimeter scanning system:\n"
        f"• 👻 <b>Detection:</b> Deleted accounts (ghosts) & invalid IDs.\n"
        f"• ⚡ <b>Configured Punishment:</b> <code>{purge_cfg.get('action', 'ban').upper()}</code>\n"
        f"• ⏳ <b>Free Tier Limit:</b> 1 scan every 24 hours (Unlimited on PRO/ULTRA).\n\n"
        f"<i>Select an action below:</i>\n\n"
        f"🛡️ <i>Cloud Media Management</i>"
    )

    keyboard = await get_purge_keyboard(group_id, callback.from_user.id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("purge_togaction_"))
async def cb_purge_toggle_action(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
    group_id = int(data[2])
    lang = data[3] if data[3] in ["es", "en"] else "es"

    if not await verify_admin_privileges(callback, bot, group_id):
        return

    cfg = await get_ghost_purge_config(group_id)
    current_action = cfg.get("action", "ban")
    new_action = "kick" if current_action == "ban" else "ban"
    
    set_ghost_purge_config(group_id, "purge_action", new_action)
    await callback.answer(f"Castigo actualizado a: {new_action.upper()} 🟢", show_alert=False)

    keyboard = await get_purge_keyboard(group_id, callback.from_user.id, lang)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("purge_run_"))
async def cb_purge_execute(callback: CallbackQuery, bot: Bot):
    data = callback.data.split("_")
    group_id = int(data[2])
    lang = data[3] if data[3] in ["es", "en"] else "es"

    if not await verify_admin_privileges(callback, bot, group_id):
        return

    tier = await get_effective_group_tier(group_id, callback.from_user.id)
    
    if tier == "free":
        can_scan = await check_can_free_purge(group_id)
        if not can_scan:
            await callback.answer(
                "⚠️ Límite diario alcanzado (Plan Free: 1 escaneo cada 24h).\nMejora a PRO o ULTRA para ejecuciones ilimitadas.",
                show_alert=True
            )
            return
        await update_ghost_purge_scan_time(group_id)

    await callback.answer("🧹 Iniciando escaneo de cuentas fantasmas...", show_alert=False)
    
    try:
        # Envía la notificación de inicio para que el comando purge_ghosts_command en groups.py se ejecute
        from handlers.groups import purge_ghosts_command
        # Creamos un objeto sintético o invocamos el proceso de escaneo directamente enviando mensaje al grupo
        await bot.send_message(
            chat_id=group_id,
            text="🧹 <b>Ghost Purge iniciado por el Administrador desde el panel privado.</b> Escaneando comunidad...",
            parse_mode="HTML"
        )
        await callback.message.answer("🚀 <b>Escaneo lanzado con éxito en la comunidad.</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error ejecutando purga rápida: {e}")


# ==========================================
# 🚀 ENRUTAMIENTO Y MANEJADORES EN PRIVADO
# ==========================================
@router.message(
    CommandStart(), 
    F.chat.type == "private", 
    ~F.text.regexp(r"^/start\s+(sub_|vipmic_|chanplan_)")
)
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
                logging.error(f"❌ [cmd_start Deeplink Grupo Error]: {g_ex}")

        elif command.args and command.args.startswith("cset_"):
            try:
                channel_id = int(command.args.split("_")[1])
                if not await verify_admin_privileges_msg(message, bot, channel_id):
                    return
                try:
                    c_name = (await bot.get_chat(channel_id)).title
                except Exception:
                    c_name = "Canal" if lang == "es" else "Channel"
                await message.answer(t["channel_panel_title"].format(channel_name=c_name), reply_markup=get_channel_panel_keyboard(channel_id, lang), parse_mode="HTML")
                return
            except Exception as c_ex:
                logging.error(f"❌ [cmd_start Deeplink Canal Error]: {c_ex}")

        await send_official_welcome(bot, message.chat.id, message.from_user, bot_username)
        logging.info(f"✅ [cmd_start ÉXITO] Matriz desplegada en @{bot_username} para {message.from_user.id}.")
    except Exception as e:
        logging.error(f"❌ [cmd_start ERROR CRÍTICO]: {e}", exc_info=True)


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
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
        resp = await message.answer(t["captcha_saved"].format(text_input=text_input), reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in CLONE_STATES:
        state_data = CLONE_STATES.pop((bot.id, user_id))
        group_id = state_data["group_id"]
        token = text_input
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=(t["btn_back_channel"] if await resolve_chat_kind(bot, group_id) == "c" else t["btn_back_group"]), callback_data=f"gset_clone_{group_id}_{lang}")]
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
                    f"• <b>Comunidad:</b> Blindada con tu réplica\n\n"
                    f"<i>¡Listo! Ya puedes abrir @{bot_username} y presionar /start.</i>\n\n"
                    f"🛡️ <i>Cloud Media Management</i>"
                )
                resp = await message.answer(success_text, reply_markup=back_kb, parse_mode="HTML")
                fire_and_forget_auto_delete([message, resp], delay=60)
            except Exception:
                try:
                    await test_bot.session.close()
                except Exception:
                    pass
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                resp = await message.answer(t["token_error"], reply_markup=back_kb, parse_mode="HTML")
                fire_and_forget_auto_delete([message, resp], delay=60)
        else:
            resp = await message.answer(t["token_error"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        return

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
            resp = await message.answer(t["phone_sent"].format(phone=res['phone']), reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [InlineKeyboardButton(text=(t["btn_back_channel"] if await resolve_chat_kind(bot, group_id) == "c" else t["btn_back_group"]), callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            error_reason = "Número no válido." if res.get("message") == "invalid_phone" else f"Telegram: {res.get('message')}"
            resp = await message.answer(t["phone_error"].format(reason=error_reason), reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        return

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
                [InlineKeyboardButton(text=(t["btn_back_channel"] if await resolve_chat_kind(bot, group_id) == "c" else t["btn_back_group"]), callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                resp = await message.answer(t["sentinel_success"], reply_markup=back_kb, parse_mode="HTML")
            else:
                resp = await message.answer(t["sentinel_error"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        elif res["status"] == "2fa_required":
            SENTINEL_2FA_STATES[(bot.id, user_id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            resp = await message.answer(t["twofa_required"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [InlineKeyboardButton(text=(t["btn_back_channel"] if await resolve_chat_kind(bot, group_id) == "c" else t["btn_back_group"]), callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            resp = await message.answer(t["code_invalid"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
        return

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
                [InlineKeyboardButton(text=(t["btn_back_channel"] if await resolve_chat_kind(bot, group_id) == "c" else t["btn_back_group"]), callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            if connected:
                await save_owner_session(user_id, group_id, session_str)
                resp = await message.answer(t["sentinel_success"], reply_markup=back_kb, parse_mode="HTML")
            else:
                resp = await message.answer(t["sentinel_error"], reply_markup=back_kb, parse_mode="HTML")
        else:
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_retry"], callback_data=f"clone_phone_{group_id}_{lang}")],
                [InlineKeyboardButton(text=(t["btn_back_channel"] if await resolve_chat_kind(bot, group_id) == "c" else t["btn_back_group"]), callback_data=f"gset_clone_{group_id}_{lang}")]
            ])
            resp = await message.answer(t["twofa_invalid"], reply_markup=cancel_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in VC_SCHED_STATES:
        sched_data = VC_SCHED_STATES.pop((bot.id, user_id))
        group_id = sched_data["group_id"]
        current_sched = await get_vc_schedule(group_id)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"vcsched_menu_{group_id}_{lang}")]
        ])
        if "-" in text_input and len(text_input.split("-")) == 2:
            parts = text_input.split("-")
            start = parts[0].strip()
            end = parts[1].strip()
            await set_vc_schedule(group_id, current_sched["days"], start, end, current_sched["status"])
            resp = await message.answer(t["sched_updated"].format(start=start, end=end), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["sched_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
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
                resp = await message.answer(t["wl_success"].format(target_id=target_id), reply_markup=back_kb, parse_mode="HTML")
            else:
                resp = await message.answer(t["id_err"], reply_markup=back_kb, parse_mode="HTML")
        else:
            word = text_input.lower()
            await add_to_blacklist(word)
            resp = await message.answer(t["bl_success"].format(word=word), reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
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
            resp = await message.answer(t["mod_id_err"], reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        try:
            if action == "ban":
                until = int(time.time() + duration) if duration > 0 else 0
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=until if until > 0 else None)
                resp = await message.answer(t["dir_ban"].format(target_id=target_id, dur_label=dur_label), reply_markup=back_kb, parse_mode="HTML")
            elif action == "kick":
                await bot.ban_chat_member(chat_id=group_id, user_id=target_id, until_date=int(time.time() + 35))
                await bot.unban_chat_member(chat_id=group_id, user_id=target_id)
                resp = await message.answer(t["dir_kick"].format(target_id=target_id), reply_markup=back_kb, parse_mode="HTML")
            elif action == "mute":
                until = int(time.time() + duration) if duration > 0 else 0
                await bot.restrict_chat_member(
                    chat_id=group_id, user_id=target_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until if until > 0 else None
                )
                resp = await message.answer(t["dir_mute"].format(target_id=target_id, dur_label=dur_label), reply_markup=back_kb, parse_mode="HTML")
            elif action == "unmute":
                await bot.restrict_chat_member(
                    chat_id=group_id, user_id=target_id,
                    permissions=ChatPermissions(
                        can_send_messages=True, can_send_audios=True, can_send_documents=True,
                        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                        can_add_web_page_previews=True
                    )
                )
                resp = await message.answer(t["dir_unmute"].format(target_id=target_id), reply_markup=back_kb, parse_mode="HTML")
        except Exception as ex:
            resp = await message.answer(t["dir_err"].format(ex=ex), reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
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
            resp = await message.answer(t["mic_updated"].format(group_id=group_id, price_val=price_val), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["mic_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in MIC_TAG_STATES:
        data = MIC_TAG_STATES.pop((bot.id, user_id))
        group_id = data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
        ])
        if 1 <= len(text_input) <= 16:
            GROUP_VIP_TAG[group_id] = text_input
            resp = await message.answer(t["tag_updated"].format(group_id=group_id, text_input=text_input), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["tag_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
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
            resp = await message.answer(t["duck_updated"].format(group_id=group_id, duck_level=duck_level), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["duck_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
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
            resp = await message.answer(t["speakers_price_updated"].format(group_id=group_id, price=price_val), reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["speakers_price_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in TIPS_AMOUNT_STATES:
        st_data = TIPS_AMOUNT_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"tips_menu_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) > 0:
            await set_tips_config(group_id, "tips_amount", int(text_input))
            resp = await message.answer(t["tips_updated"], reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["tips_amount_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in TIPS_TARGET_STATES:
        st_data = TIPS_TARGET_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"tips_menu_{group_id}_{lang}")]
        ])
        clean_target = text_input.replace("https://t.me/", "").replace("t.me/", "").strip()
        if clean_target:
            await set_tips_config(group_id, "tips_target_channel", clean_target)
            resp = await message.answer(t["tips_updated"], reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["tips_target_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in SENTINEL_PAYLOAD_TEXT_STATES:
        st_data = SENTINEL_PAYLOAD_TEXT_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"payload_menu_{group_id}_{lang}")]
        ])
        await set_sentinel_payload_config(group_id, "sentinel_payload_text", text_input)
        resp = await message.answer(t["sentinel_payload_saved"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in SENTINEL_PAYLOAD_AUTODEL_STATES:
        st_data = SENTINEL_PAYLOAD_AUTODEL_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"payload_menu_{group_id}_{lang}")]
        ])
        if text_input.isdigit() and int(text_input) >= 0:
            val = int(text_input)
            await set_sentinel_payload_config(group_id, "sentinel_payload_auto_delete", val if val > 0 else None)
            resp = await message.answer(t["sentinel_payload_saved"], reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["sentinel_payload_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in SENTINEL_PAYLOAD_MEDIA_STATES:
        st_data = SENTINEL_PAYLOAD_MEDIA_STATES.pop((bot.id, user_id))
        group_id = st_data["group_id"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_group"], callback_data=f"payload_menu_{group_id}_{lang}")]
        ])
        media_id = None
        media_type = None

        if message.photo:
            media_id = message.photo[-1].file_id
            media_type = "photo"
        elif message.animation:
            media_id = message.animation.file_id
            media_type = "animation"
        elif message.video:
            media_id = message.video.file_id
            media_type = "video"

        if media_id and media_type:
            await set_sentinel_payload_config(group_id, "sentinel_payload_media_id", media_id)
            await set_sentinel_payload_config(group_id, "sentinel_payload_media_type", media_type)
            resp = await message.answer(t["sentinel_payload_saved"], reply_markup=back_kb, parse_mode="HTML")
        else:
            resp = await message.answer(t["sentinel_payload_err"], reply_markup=back_kb, parse_mode="HTML")
        fire_and_forget_auto_delete([message, resp], delay=60)
        return

    if (bot.id, user_id) in CHAN_PLAN_STATES:
        st_data = CHAN_PLAN_STATES[(bot.id, user_id)]
        channel_id = st_data["channel_id"]
        step = st_data["step"]
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")]
        ])

        if step == "name":
            plan_name = text_input[:30].strip()
            if not plan_name:
                resp = await message.answer("⚠️ El nombre del plan no puede estar vacío.", reply_markup=back_kb)
                fire_and_forget_auto_delete([message, resp], delay=60)
                return
            CHAN_PLAN_STATES[(bot.id, user_id)]["name"] = plan_name
            CHAN_PLAN_STATES[(bot.id, user_id)]["step"] = "days"
            prompt_days = "⏳ <b>Duración del plan en días:</b>\n\nEnvía un número entero (ejemplo: <code>30</code> para un mes):" if lang == "es" else "⏳ <b>Plan duration in days:</b>\n\nSend an integer (e.g. <code>30</code>):"
            resp = await message.answer(prompt_days, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "days":
            if not text_input.isdigit() or int(text_input) <= 0:
                resp = await message.answer("⚠️ Ingresa un número entero de días válido.", reply_markup=back_kb)
                fire_and_forget_auto_delete([message, resp], delay=60)
                return
            CHAN_PLAN_STATES[(bot.id, user_id)]["days"] = int(text_input)
            CHAN_PLAN_STATES[(bot.id, user_id)]["step"] = "price"
            prompt_price = "⭐ <b>Precio en Telegram Stars (XTR):</b>\n\nEnvía la tarifa en Stars que costará la membresía (ejemplo: <code>150</code>):" if lang == "es" else "⭐ <b>Price in Telegram Stars (XTR):</b>\n\nSend the price in Stars (e.g. <code>150</code>):"
            resp = await message.answer(prompt_price, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "price":
            if not text_input.isdigit() or int(text_input) <= 0:
                resp = await message.answer("⚠️ Ingresa un precio entero válido en Stars.", reply_markup=back_kb)
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            CHAN_PLAN_STATES[(bot.id, user_id)]["price"] = int(text_input)
            CHAN_PLAN_STATES[(bot.id, user_id)]["step"] = "target_link"
            prompt_link = (
                "🔗 <b>Enlace de Destino o Canal/Grupo VIP (Opcional):</b>\n\n"
                "Envía el enlace público o privado al que se le dará acceso o que se promocionará con este plan "
                "(ejemplo: <code>https://t.me/+AbCdEf...</code> o <code>@MiCanalVIP</code>).\n\n"
                "<i>Escribe <code>omitir</code> si no deseas adjuntar un enlace.</i>"
            ) if lang == "es" else (
                "🔗 <b>Target Link or VIP Channel/Group (Optional):</b>\n\n"
                "Send the public or private link to be promoted or granted access with this plan "
                "(e.g. <code>https://t.me/+AbCdEf...</code> or <code>@MyVIPChannel</code>).\n\n"
                "<i>Send <code>skip</code> if you don't wish to attach a target link.</i>"
            )
            resp = await message.answer(prompt_link, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "target_link":
            raw_link = text_input.strip()
            if raw_link.lower() in ("omitir", "skip", "ninguno", "none", "-"):
                target_link = None
            else:
                if raw_link.startswith("@"):
                    target_link = f"https://t.me/{raw_link.lstrip('@')}"
                elif not raw_link.startswith("http://") and not raw_link.startswith("https://"):
                    target_link = f"https://{raw_link}"
                else:
                    target_link = raw_link
                target_link = target_link[:255]

            CHAN_PLAN_STATES[(bot.id, user_id)]["target_link"] = target_link
            CHAN_PLAN_STATES[(bot.id, user_id)]["step"] = "promo"

            prompt_promo = (
                "📝 <b>Mensaje promocional (Copy):</b>\n\n"
                "Envía el texto que verán tus suscriptores antes de pagar. Soporta formato HTML "
                "(<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;a href&gt;</code>, etc.):"
            ) if lang == "es" else (
                "📝 <b>Promotional message (Copy):</b>\n\n"
                "Send the text your subscribers will see before paying. HTML formatting is supported "
                "(<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;a href&gt;</code>, etc.):"
            )
            resp = await message.answer(prompt_promo, reply_markup=back_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "promo":
            promo_text = text_input[:1000].strip()
            if not promo_text:
                resp = await message.answer("⚠️ El mensaje promocional no puede estar vacío.", reply_markup=back_kb)
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            CHAN_PLAN_STATES[(bot.id, user_id)]["promo"] = promo_text
            CHAN_PLAN_STATES[(bot.id, user_id)]["step"] = "media"

            skip_label = "⏭️ Omitir Multimedia" if lang == "es" else "⏭️ Skip Media"
            media_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=skip_label, callback_data=f"chplans_skip_{channel_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")]
            ])
            prompt_media = (
                "🖼️ <b>Multimedia del anuncio (opcional):</b>\n\n"
                "Envía una Foto, Video o Animación/GIF para acompañar el mensaje promocional, "
                "o pulsa «Omitir» para publicarlo solo en texto:"
            ) if lang == "es" else (
                "🖼️ <b>Promo media (optional):</b>\n\n"
                "Send a Photo, Video or Animation/GIF to accompany the promo message, "
                "or tap «Skip» to publish it as text only:"
            )
            resp = await message.answer(prompt_media, reply_markup=media_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([message, resp], delay=60)
            return

        elif step == "media":
            media_id = None
            media_type = None
            if message.photo:
                media_id = message.photo[-1].file_id
                media_type = "photo"
            elif message.video:
                media_id = message.video.file_id
                media_type = "video"
            elif message.animation:
                media_id = message.animation.file_id
                media_type = "animation"

            if not media_id:
                skip_label = "⏭️ Omitir Multimedia" if lang == "es" else "⏭️ Skip Media"
                retry_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=skip_label, callback_data=f"chplans_skip_{channel_id}_{lang}")],
                    [InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")]
                ])
                warn_text = (
                    "⚠️ Envía una Foto, Video o Animación válida, o pulsa «Omitir»."
                    if lang == "es" else
                    "⚠️ Send a valid Photo, Video or Animation, or tap «Skip»."
                )
                resp = await message.answer(warn_text, reply_markup=retry_kb, parse_mode="HTML")
                fire_and_forget_auto_delete([message, resp], delay=60)
                return

            plan_name = st_data["name"]
            duration_days = st_data["days"]
            price_stars = st_data["price"]
            promo_text = st_data.get("promo", "")
            target_link = st_data.get("target_link")
            CHAN_PLAN_STATES.pop((bot.id, user_id), None)

            await _finalize_and_preview_channel_plan(
                bot=bot,
                chat_id=message.chat.id,
                channel_id=channel_id,
                lang=lang,
                name=plan_name,
                days=duration_days,
                price=price_stars,
                promo_text=promo_text,
                media_id=media_id,
                media_type=media_type,
                target_link=target_link
            )
            return


@router.callback_query(
    F.data.startswith("menu_") | F.data.startswith("lang_") | F.data.startswith("langpanel_") | 
    F.data.startswith("langcpanel_") | F.data.startswith("gpanel_") | F.data.startswith("cpanel_") | 
    F.data.startswith("cmd_") | F.data.startswith("pay_") | F.data.startswith("time_") | 
    F.data.startswith("clone_") | F.data.startswith("alset_") | F.data.startswith("micval_") | 
    F.data.startswith("reg_") | F.data.startswith("vcsched_") | F.data.startswith("tips_") |
    F.data.startswith("night_")
)
async def process_menu_navigation(callback: CallbackQuery, bot: Bot):
    for state_dict in [
        CAPTCHA_STATES, CLONE_STATES, SENTINEL_PHONE_STATES, SENTINEL_CODE_STATES, 
        SENTINEL_2FA_STATES, VC_SCHED_STATES, DB_REG_STATES, MOD_TARGET_STATES, 
        MIC_VIP_STATES, MIC_TAG_STATES, PODCAST_DUCK_STATES, SPEAKER_PRICE_STATES,
        TIPS_AMOUNT_STATES, TIPS_TARGET_STATES, SENTINEL_PAYLOAD_TEXT_STATES,
        SENTINEL_PAYLOAD_MEDIA_STATES, SENTINEL_PAYLOAD_AUTODEL_STATES,
        NIGHT_STATES, CHAN_PLAN_STATES
    ]:
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

    elif action == "langcpanel":
        channel_id = int(data[1])
        lang = data[2]
        t = TEXTS.get(lang, TEXTS["es"])
        if not await verify_admin_privileges(callback, bot, channel_id):
            return
        try:
            c_name = (await bot.get_chat(channel_id)).title
        except Exception:
            c_name = "Canal" if lang == "es" else "Channel"
        text = t["channel_panel_title"].format(channel_name=c_name)
        keyboard = get_channel_panel_keyboard(channel_id, lang)

    elif action == "cpanel":
        channel_id = int(data[1])
        if not await verify_admin_privileges(callback, bot, channel_id):
            return
        try:
            c_name = (await bot.get_chat(channel_id)).title
        except Exception:
            c_name = "Canal" if lang == "es" else "Channel"
        text = t["channel_panel_title"].format(channel_name=c_name)
        keyboard = get_channel_panel_keyboard(channel_id, lang)

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
        
    elif action == "menu":
        target = data[1]
        if target == "main":
            text = t["welcome"].format(name=callback.from_user.full_name)
            keyboard = get_main_keyboard((await bot.get_me()).username, lang, is_clone=is_clone_bot(bot))
        elif target == "settings":
            active_groups = await get_active_user_groups(bot, callback.from_user.id)
            text, keyboard = t["settings_main"], get_groups_keyboard(active_groups, lang)
        elif target == "chsettings":
            active_channels = await get_active_user_channels(bot, callback.from_user.id)
            text, keyboard = t["chsettings_main"], get_channels_keyboard(active_channels, lang)
        elif target == "support":
            text, keyboard = t["support_main"], get_support_keyboard(lang)
        elif target == "info":
            text, keyboard = t["info_main"], get_info_keyboard(lang)
        elif target in ["infohow", "info_how"]:
            text, keyboard = t["info_how_main"], InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛡️ " + ("Grupos y Perímetro" if lang == "es" else "Groups & Perimeter"), callback_data=f"menu_infomod_groups_{lang}")],
                [InlineKeyboardButton(text="📡 " + ("Canales y Lives" if lang == "es" else "Channels & Lives"), callback_data=f"menu_infomod_channels_{lang}")],
                [InlineKeyboardButton(text="💰 " + ("Monetización Stars" if lang == "es" else "Stars Monetization"), callback_data=f"menu_infomod_monetization_{lang}")],
                [InlineKeyboardButton(text="🧬 " + ("Clon & Centinela" if lang == "es" else "Clone & Sentinel"), callback_data=f"menu_infomod_clones_{lang}")],
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
            ])
        elif target == "infomod":
            mod_name = data[2]
            mod_text_key = f"info_mod_{mod_name}"
            mod_desc = t.get(mod_text_key, t["info_how_main"])
            text = f"📖 <b>Centro de Conocimiento</b>\n\n{mod_desc}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Volver a Guías" if lang == "es" else "🔙 Back to Guides", callback_data=f"menu_infohow_{lang}")],
                [InlineKeyboardButton(text=t["btn_back"], callback_data=f"menu_main_{lang}")]
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
            chat_kind = await resolve_chat_kind(bot, group_id)
            text = t["ultra_tools_main"].format(group_name=g_name)
            keyboard = get_ultra_tools_keyboard(group_id, lang, chat_type=chat_kind)

    elif action == "tips":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        chat_kind = await resolve_chat_kind(bot, group_id)
        cfg = await get_tips_config(group_id)
        if sub == "menu":
            st_badge = "🟢 ACTIVADAS" if cfg.get("enabled") == 1 else "🔴 DESACTIVADAS"
            target_str = cfg.get("target_channel") or ("No configurado" if lang == "es" else "Not set")
            text = t["tips_main"].format(st_badge=st_badge, amount=cfg.get("amount", 10), target=target_str)
            keyboard = get_tips_keyboard(group_id, lang, cfg, chat_type=chat_kind)
        elif sub == "toggle":
            new_st = 0 if cfg.get("enabled") == 1 else 1
            await set_tips_config(group_id, "tips_enabled", new_st)
            cfg = await get_tips_config(group_id)
            st_badge = "🟢 ACTIVADAS" if cfg.get("enabled") == 1 else "🔴 DESACTIVADAS"
            target_str = cfg.get("target_channel") or ("No configurado" if lang == "es" else "Not set")
            text = t["tips_main"].format(st_badge=st_badge, amount=cfg.get("amount", 10), target=target_str)
            keyboard = get_tips_keyboard(group_id, lang, cfg, chat_type=chat_kind)
        elif sub == "setamount":
            TIPS_AMOUNT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["tips_prompt_amount"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "settarget":
            TIPS_TARGET_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["tips_prompt_target"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    elif action == "pay":
        tier_level = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        try:
            g_name = (await bot.get_chat(group_id)).title
        except Exception:
            g_name = "Comunidad" if lang == "es" else "Community"
        chat_kind = await resolve_chat_kind(bot, group_id)
        text = t[f"pay_{tier_level}_title"].format(group_name=g_name)
        keyboard = get_payment_keyboard(group_id, lang, tier_level=tier_level, chat_type=chat_kind)

    elif action == "vcsched":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            sched_lock_text = "🗓️ <b>Programador VC (ULTRA PRO)</b>\n\n🔒 <i>Exclusivo del nivel ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Desbloquear con ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
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
            text = t["vcsched_main"].format(st_badge=st_badge, days=sched['days'], start=sched['start_time'], end=sched['end_time'])
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_sched_off"] if sched["status"] == 1 else t["btn_sched_on"], callback_data=f"vcsched_toggle_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_sched_mod"], callback_data=f"vcsched_timeprompt_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub == "timeprompt":
            VC_SCHED_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["vcsched_prompt"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    elif action == "clone":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        chat_kind = await resolve_chat_kind(bot, group_id)
        back_label = t["btn_back_channel"] if chat_kind == "c" else t["btn_back_group"]

        if sub in ("token", "phone"):
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                clone_lock_text = "🧬 <b>Clonación & Centinela (ULTRA PRO)</b>\n\n🔒 <i>Exclusivo del nivel ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Desbloquear con ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
                    [InlineKeyboardButton(text=back_label, callback_data=f"gset_clone_{group_id}_{lang}")]
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
            prompt = await callback.message.answer(t["botfather_guide"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "phone":
            SENTINEL_PHONE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_cancel_ret"], callback_data=f"clone_cancel_{group_id}_{lang}")]
            ])
            prompt = await callback.message.answer(t["sentinel_phone_guide"], reply_markup=cancel_kb, parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
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
            status = "Operativo 🟢" if has_clone else "No Configurado 🔴"
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = "Conectado 🟢" if session_info else "No Configurado 🔴"
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind)
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
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = "Conectado 🟢" if session_info else "No Configurado 🔴"
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status="Desconectado 🔴", sentinel_status=sentinel_status)
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind)
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
            status = "Operativo 🟢" if has_clone else "No Configurado 🔴"
            text = t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status="Desconectado 🔴")
            keyboard = await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind)

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
                al_lock_text = "🔇 <b>Radar AutoLower (ULTRA PRO)</b>\n\n🔒 <i>Disponible exclusivamente en ULTRA PRO.</i>\n\n🛡️ <i>Cloud Media Management</i>"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Desbloquear con ULTRA", callback_data=f"pay_ultra_{group_id}_{lang}")],
                    [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
                ])
                try:
                    await callback.message.edit_text(al_lock_text, reply_markup=keyboard, parse_mode="HTML")
                except TelegramBadRequest:
                    pass
                return

            curr_al = await get_autolower_status(group_id)
            status_str = "🟢 ACTIVADO (2% para no autorizados)" if curr_al == 1 else "🔴 DESACTIVADO (Micrófonos Libres)"
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
                [InlineKeyboardButton(text=t["btn_mictag"].format(curr_tag=curr_tag), callback_data=f"cmd_mictag_{group_id}_{lang}")],
                [InlineKeyboardButton(text=t["btn_back_eco"], callback_data=f"menu_eco_{group_id}_{lang}")]
            ])
        elif sub_cmd == "mictag":
            tier = await get_effective_group_tier(group_id, callback.from_user.id)
            if tier != "ultra_pro":
                await callback.answer(t["tag_pro_req"], show_alert=True)
                return
            MIC_TAG_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            curr_tag = GROUP_VIP_TAG.get(group_id, "VIP 24/7")
            prompt = await callback.message.answer(t["tag_menu_prompt"].format(curr_tag=curr_tag), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub_cmd in ["ban", "mute"]:
            text = t["mod_ask_time"].format(sub_cmd=sub_cmd)
            keyboard = get_time_selection_keyboard(sub_cmd, group_id, lang)
        elif sub_cmd in ["kick", "unmute"]:
            MOD_TARGET_STATES[(bot.id, callback.from_user.id)] = {
                "action": sub_cmd, "group_id": group_id, "duration": 0, 
                "dur_label": "Inmediato" if lang == "es" else "Immediate", "lang": lang
            }
            prompt = await callback.message.answer(t["mod_ask_target"].format(sub_cmd_upper=sub_cmd.upper()), parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    elif action == "reg":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        DB_REG_STATES[(bot.id, callback.from_user.id)] = {"type": sub, "group_id": group_id, "lang": lang}
        target_name = t["reg_ask_wl"] if sub == "wl" else t["reg_ask_bl"]
        prompt = await callback.message.answer(t["reg_ask"].format(target_name=target_name), parse_mode="HTML")
        fire_and_forget_auto_delete([prompt], delay=60)
        return

    elif action == "alset":
        new_st = int(data[1])
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            await callback.answer("🔒 Requiere ULTRA PRO.", show_alert=True)
            return
        await set_autolower_status(group_id, new_st)
        await callback.answer(t["al_updated_1"] if new_st == 1 else t["al_updated_0"])
        curr_al = await get_autolower_status(group_id)
        status_str = "🟢 ACTIVADO (2% para no autorizados)" if curr_al == 1 else "🔴 DESACTIVADO (Micrófonos Libres)"
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
            prompt = await callback.message.answer(t["mic_custom_prompt"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
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
                [InlineKeyboardButton(text=t["btn_mictag"].format(curr_tag=curr_tag), callback_data=f"cmd_mictag_{group_id}_{lang}")],
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
        prompt = await callback.message.answer(t["mod_ask_target"].format(sub_cmd_upper=f"{sub_cmd.upper()} ({label})"), parse_mode="HTML")
        fire_and_forget_auto_delete([prompt], delay=60)
        return

    elif action == "night":
        sub = data[1]
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return

        if sub == "menu" or sub == "toggle":
            if sub == "toggle":
                new_st = int(data[3])
                await set_night_mode_config(group_id, "night_mode_status", new_st)
            
            cfg = await get_night_mode_config(group_id)
            st_badge = "🟢 ACTIVADO" if cfg["status"] == 1 else "🔴 DESACTIVADO"
            if lang == "en":
                st_badge = "🟢 ACTIVE" if cfg["status"] == 1 else "🔴 DISABLED"

            text = t["night_main"].format(st_badge=st_badge, start=cfg["start"], end=cfg["end"], action=cfg["action"])
            keyboard = get_night_keyboard(group_id, lang, cfg["status"])
            try:
                await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            except TelegramBadRequest:
                pass
        elif sub == "prompt":
            NIGHT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["night_prompt"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

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
            await callback.message.edit_text(
                t["delmsgs_main_title"].format(tier_display=tier_display, quota_desc=quota_desc),
                reply_markup=await get_delmsgs_keyboard(group_id, callback.from_user.id, lang),
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
                reply_markup=await get_antiflood_keyboard(group_id, lang, cfg), 
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
            status = f"Operativo 🟢" if has_clone else "No Configurado 🔴"
            session_info = await get_owner_session(callback.from_user.id, group_id)
            sentinel_status = "Conectado 🟢" if session_info else "No Configurado 🔴"
            chat_kind = await resolve_chat_kind(bot, group_id)

            await callback.message.edit_text(
                t["clone_main_title"].format(group_name=g_name, tier=tier.upper(), status=status, sentinel_status=sentinel_status),
                reply_markup=await get_clone_keyboard(group_id, callback.from_user.id, lang, chat_type=chat_kind),
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
        elif sub == "srvmode":
            current_srv = await get_service_msgs_mode(group_id)
            await set_service_msgs_mode(group_id, 0 if current_srv == 1 else 1)
        elif sub == "main":
            if tier == "free":
                await callback.answer("⚠️ Requiere nivel PRO o ULTRA.", show_alert=True)
                return
            current_del = await get_antispam_delete(group_id)
            await set_antispam_delete(group_id, 0 if current_del == 1 else 1)
        elif sub == "cmds":
            if tier == "free":
                await callback.answer("⚠️ Requiere nivel PRO o ULTRA.", show_alert=True)
                return
            current_cmd = await get_lock_status(group_id, "lock_commands")
            await set_lock_status(group_id, "lock_commands", 0 if current_cmd == 1 else 1)
        
        try:
            await callback.message.edit_reply_markup(reply_markup=await get_delmsgs_keyboard(group_id, callback.from_user.id, lang))
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
        elif sub == "toglink":
            current = cfg.get("warn_links", 1)
            await set_warns_config(group_id, "warn_links", 0 if current == 1 else 1)
        elif sub == "togblack":
            current = cfg.get("warn_blacklist", 1)
            await set_warns_config(group_id, "warn_blacklist", 0 if current == 1 else 1)
        elif sub == "togflood":
            current = cfg.get("warn_flood", 1)
            await set_warns_config(group_id, "warn_flood", 0 if current == 1 else 1)

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
            prompt = "⏱️ <b>Configuración de Tiempo Límite</b>" if lang == "es" else "⏱️ <b>Time Limit Configuration</b>"
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
                upsell_text = "⭐ <b>Aduana Captcha Pro</b>\n\nPersonalizar el mensaje requiere nivel PRO o ULTRA PRO.\n\n🛡️ <i>Cloud Media Management</i>"
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
            prompt = await callback.message.answer("✍️ <b>Editor de Captcha</b>\n\nEnvía el mensaje que recibirá el usuario al ingresar:\n\n🛡️ <i>Cloud Media Management</i>", parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
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
                        reply_markup=await get_delmsgs_keyboard(group_id, callback.from_user.id, lang),
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
        prompt = f"<b>{t['af_msgs']}</b>\nSelecciona el límite:"
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


@router.callback_query(
    F.data.startswith("panic_") | F.data.startswith("shield_") |
    F.data.startswith("podcast_") | F.data.startswith("speakers_") |
    F.data.startswith("payload_")
)
async def cb_ultra_tools_dispatch(callback: CallbackQuery, bot: Bot):
    for state_dict in [
        PODCAST_DUCK_STATES, SPEAKER_PRICE_STATES,
        SENTINEL_PAYLOAD_TEXT_STATES, SENTINEL_PAYLOAD_MEDIA_STATES, SENTINEL_PAYLOAD_AUTODEL_STATES
    ]:
        state_dict.pop((bot.id, callback.from_user.id), None)

    data = callback.data.split("_")
    module = data[0]
    sub = data[1]
    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    text = ""
    keyboard = None

    if module == "panic":
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🚨 <b>Botón de Pánico</b>" if lang == "es" else "🚨 <b>Panic Button</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            status = await get_panic_status(group_id)
            status_str = "🚨 BLOQUEADO" if status == 1 else "🟢 Normal"
            text = t["panic_menu"].format(status_str=status_str)
            keyboard = get_panic_keyboard(group_id, lang, status, chat_type=chat_kind)
        elif sub == "confirm":
            text = t["panic_confirm"]
            keyboard = get_panic_confirm_keyboard(group_id, lang)
        elif sub == "activate":
            await set_panic_status(group_id, 1)
            try:
                await execute_raid_lockdown(bot, group_id)
            except Exception as ex:
                logging.error(f"❌ [Panic] Fallo lockdown en {group_id}: {ex}")
            text = t["panic_activated"]
            keyboard = get_panic_keyboard(group_id, lang, 1, chat_type=chat_kind)
        elif sub == "deactivate":
            await set_panic_status(group_id, 0)
            try:
                await lift_raid_lockdown(bot, group_id)
            except Exception as ex:
                logging.error(f"❌ [Panic] Fallo levantar lockdown en {group_id}: {ex}")
            text = t["panic_deactivated"]
            keyboard = get_panic_keyboard(group_id, lang, 0, chat_type=chat_kind)

    elif module == "shield":
        if sub == "toggle":
            new_status = int(data[2])
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🎥 <b>Escudo Antinota</b>" if lang == "es" else "🎥 <b>Screen-Share Shield</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            status = await get_shield_status(group_id)
            status_str = "🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO"
            text = t["shield_menu"].format(status_str=status_str)
            keyboard = get_shield_keyboard(group_id, lang, status, chat_type=chat_kind)
        elif sub == "toggle":
            await set_shield_status(group_id, new_status)
            try:
                if new_status == 1:
                    await engage_screen_shield(group_id)
                else:
                    await disengage_screen_shield(group_id)
            except Exception as ex:
                logging.error(f"❌ [Shield] Fallo al aplicar escudo en {group_id}: {ex}")
            await callback.answer(t["shield_updated_1"] if new_status == 1 else t["shield_updated_0"])
            status = await get_shield_status(group_id)
            status_str = "🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO"
            text = t["shield_menu"].format(status_str=status_str)
            keyboard = get_shield_keyboard(group_id, lang, status, chat_type=chat_kind)

    elif module == "podcast":
        if sub in ("toggle", "duckval"):
            val = data[2]
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🎙️ <b>Modo Podcast & Audio Ducking</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            status = await get_podcast_status(group_id)
            duck_level = GROUP_DUCK_LEVEL.get(group_id, 20)
            status_str = "🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO"
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level)
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level, chat_type=chat_kind)
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
                logging.error(f"❌ [Podcast] Fallo ducking en {group_id}: {ex}")
            await callback.answer(t["podcast_updated_1"] if new_status == 1 else t["podcast_updated_0"])
            status = await get_podcast_status(group_id)
            status_str = "🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO"
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level)
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level, chat_type=chat_kind)
        elif sub == "duckval":
            duck_level = int(val)
            GROUP_DUCK_LEVEL[group_id] = duck_level
            status = await get_podcast_status(group_id)
            if status == 1:
                try:
                    await disengage_podcast_ducking(group_id)
                    await engage_podcast_ducking(group_id, duck_level)
                except Exception as ex:
                    logging.error(f"❌ [Podcast] Fallo actualizacion ducking en {group_id}: {ex}")
            await callback.answer(t["duck_updated"].format(group_id=group_id, duck_level=duck_level), show_alert=True)
            status_str = "🟢 ACTIVADO" if status == 1 else "🔴 DESACTIVADO"
            text = t["podcast_menu"].format(status_str=status_str, duck_level=duck_level)
            keyboard = get_podcast_keyboard(group_id, lang, status, duck_level, chat_type=chat_kind)
        elif sub == "duckset":
            PODCAST_DUCK_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["duck_custom_prompt"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    elif module == "speakers":
        if sub in ("toggle", "priceval"):
            val = data[2]
            group_id = int(data[3])
        else:
            group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "🌟 <b>Cola de Speakers Pagada</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = "🟢 ACTIVA"
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count)
            keyboard = get_speakers_keyboard(group_id, lang, 1, price, chat_type=chat_kind)
        elif sub == "toggle":
            new_status = int(val)
            await callback.answer(t["speakers_updated_1"] if new_status == 1 else t["speakers_updated_0"])
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = "🟢 ACTIVADA" if new_status == 1 else "🔴 DESACTIVADA"
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count)
            keyboard = get_speakers_keyboard(group_id, lang, new_status, price, chat_type=chat_kind)
        elif sub == "priceval":
            price = int(val)
            GROUP_SPEAKER_PRICE[group_id] = price
            await callback.answer(t["speakers_price_updated"].format(group_id=group_id, price=price), show_alert=True)
            queue = await get_speaker_queue(group_id)
            queue_count = len(queue) if queue else 0
            status_str = "🟢 ACTIVA"
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=queue_count)
            keyboard = get_speakers_keyboard(group_id, lang, 1, price, chat_type=chat_kind)
        elif sub == "priceset":
            SPEAKER_PRICE_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["speakers_price_prompt"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "clear":
            try:
                await clear_speaker_queue(group_id)
            except Exception as ex:
                logging.error(f"❌ [Speakers] Fallo vaciado cola en {group_id}: {ex}")
            await callback.answer(t["speakers_cleared"])
            price = GROUP_SPEAKER_PRICE.get(group_id, 20)
            status_str = "🟢 ACTIVA"
            text = t["speakers_menu"].format(status_str=status_str, price=price, queue_count=0)
            keyboard = get_speakers_keyboard(group_id, lang, 1, price, chat_type=chat_kind)

    elif module == "payload":
        group_id = int(data[2])
        if not await verify_admin_privileges(callback, bot, group_id):
            return
        chat_kind = await resolve_chat_kind(bot, group_id)

        tier = await get_effective_group_tier(group_id, callback.from_user.id)
        if tier != "ultra_pro":
            feature_title = "💎 <b>Payload Multimedia del Centinela</b>"
            text, keyboard = build_ultra_lock_view(group_id, lang, feature_title)
        elif sub == "menu":
            cfg = await get_sentinel_payload_config(group_id)
            st_badge = "🟢 ACTIVADO" if cfg.get("enabled") == 1 else "🔴 DESACTIVADO"
            has_text = "🟢" if cfg.get("text") else "🔴"
            has_media = f"🟢 ({cfg.get('media_type')})" if cfg.get("media_id") else "🔴"
            autodel = f"{cfg.get('auto_delete_after')}s" if cfg.get("auto_delete_after") else "Off"
            text = t["sentinel_payload_main"].format(st_badge=st_badge, has_text=has_text, has_media=has_media, autodel=autodel)
            keyboard = get_sentinel_payload_keyboard(group_id, lang, cfg, chat_type=chat_kind)
        elif sub == "toggle":
            cfg = await get_sentinel_payload_config(group_id)
            new_st = 0 if cfg.get("enabled") == 1 else 1
            await set_sentinel_payload_config(group_id, "sentinel_payload_enabled", new_st)
            cfg = await get_sentinel_payload_config(group_id)
            st_badge = "🟢 ACTIVADO" if cfg.get("enabled") == 1 else "🔴 DESACTIVADO"
            has_text = "🟢" if cfg.get("text") else "🔴"
            has_media = f"🟢 ({cfg.get('media_type')})" if cfg.get("media_id") else "🔴"
            autodel = f"{cfg.get('auto_delete_after')}s" if cfg.get("auto_delete_after") else "Off"
            text = t["sentinel_payload_main"].format(st_badge=st_badge, has_text=has_text, has_media=has_media, autodel=autodel)
            keyboard = get_sentinel_payload_keyboard(group_id, lang, cfg, chat_type=chat_kind)
        elif sub == "text":
            SENTINEL_PAYLOAD_TEXT_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["sentinel_payload_prompt_text"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "media":
            SENTINEL_PAYLOAD_MEDIA_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["sentinel_payload_prompt_media"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return
        elif sub == "autodel":
            SENTINEL_PAYLOAD_AUTODEL_STATES[(bot.id, callback.from_user.id)] = {"group_id": group_id, "lang": lang}
            prompt = await callback.message.answer(t["sentinel_payload_prompt_del"], parse_mode="HTML")
            fire_and_forget_auto_delete([prompt], delay=60)
            return

    if text and keyboard:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _finalize_and_preview_channel_plan(
    bot: Bot, chat_id: int, channel_id: int, lang: str,
    name: str, days: int, price: int, promo_text: str,
    media_id: str = None, media_type: str = None,
    target_link: str = None
):
    t = TEXTS.get(lang, TEXTS["es"])

    plan_id = await create_channel_plan(
        channel_id=channel_id, 
        plan_name=name, 
        duration_days=days, 
        stars_price=price,
        promo_text=promo_text, 
        media_id=media_id, 
        media_type=media_type,
        target_link=target_link
    )

    bot_info = await bot.get_me()
    deep_link = f"https://t.me/{bot_info.username}?start=chanplan_{plan_id}_{channel_id}"

    buy_label = f"💳 Suscribirme — {price} ⭐" if lang == "es" else f"💳 Subscribe — {price} ⭐"
    preview_kb_rows = [
        [InlineKeyboardButton(text=buy_label, url=deep_link)]
    ]
    if target_link:
        link_url = target_link if target_link.startswith("http") else f"https://t.me/{target_link.lstrip('@')}"
        preview_kb_rows.append([
            InlineKeyboardButton(text="🔗 Ver Destino / Canal VIP" if lang == "es" else "🔗 View Target / VIP", url=link_url)
        ])
    preview_kb = InlineKeyboardMarkup(inline_keyboard=preview_kb_rows)

    caption_text = promo_text.strip() if promo_text and promo_text.strip() else (
        f"💎 <b>{name}</b>\n\n{days} días — {price} ⭐" if lang == "es" else f"💎 <b>{name}</b>\n\n{days} days — {price} ⭐"
    )
    if target_link:
        caption_text += f"\n\n🔗 <b>Acceso / Destino:</b> <code>{target_link}</code>" if lang == "es" else f"\n\n🔗 <b>Target / Access:</b> <code>{target_link}</code>"

    try:
        if media_id and media_type == "photo":
            await bot.send_photo(chat_id=chat_id, photo=media_id, caption=caption_text, reply_markup=preview_kb, parse_mode="HTML")
        elif media_id and media_type == "video":
            await bot.send_video(chat_id=chat_id, video=media_id, caption=caption_text, reply_markup=preview_kb, parse_mode="HTML")
        elif media_id and media_type == "animation":
            await bot.send_animation(chat_id=chat_id, animation=media_id, caption=caption_text, reply_markup=preview_kb, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=chat_id, text=caption_text, reply_markup=preview_kb, parse_mode="HTML")
    except Exception as e:
        logging.warning(f"Aviso: fallo al despachar vista previa del plan {plan_id} en canal {channel_id}: {e}")

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")]
    ])
    
    target_info = f"\n• <b>Enlace / Destino:</b> <code>{target_link}</code>" if target_link else ""
    done_text = (
        f"✅ <b>¡Plan de Membresía Creado con Éxito!</b>\n\n"
        f"• <b>Plan:</b> {name}\n"
        f"• <b>Duración:</b> {days} días\n"
        f"• <b>Precio:</b> {price} Stars (XTR){target_info}\n\n"
        f"👆 <i>La vista previa de arriba es el mensaje exacto que verán tus suscriptores. Puedes pinearlo o reenviarlo a tu canal.</i>\n\n"
        f"🔗 <b>Enlace directo:</b>\n<code>{deep_link}</code>"
    ) if lang == "es" else (
        f"✅ <b>Membership Plan Created Successfully!</b>\n\n"
        f"• <b>Plan:</b> {name}\n"
        f"• <b>Duration:</b> {days} days\n"
        f"• <b>Price:</b> {price} Stars (XTR){target_info}\n\n"
        f"👆 <i>The preview above is the exact message your subscribers will see. Pin or forward it to your channel.</i>\n\n"
        f"🔗 <b>Direct link:</b>\n<code>{deep_link}</code>"
    )
    await bot.send_message(chat_id=chat_id, text=done_text, reply_markup=back_kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("chplans_"))
async def cb_channel_plans_dispatch(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    data = callback.data.split("_")
    sub = data[1]
    
    if sub == "del":
        plan_id = int(data[2])
        channel_id = int(data[3])
    else:
        channel_id = int(data[2])

    lang = data[-1] if data[-1] in ["es", "en"] else "es"
    t = TEXTS.get(lang, TEXTS["es"])

    if not await verify_admin_privileges(callback, bot, channel_id):
        return

    if sub == "menu":
        plans = await get_channel_plans(channel_id, only_active=True)
        sub_count = await get_active_subscribers_count(channel_id)
        bot_info = await bot.get_me()

        try:
            c_name = (await bot.get_chat(channel_id)).title
        except Exception:
            c_name = "Canal"

        plans_list_text = ""
        kb_rows = []
        if plans:
            for p in plans:
                p_id, p_name, p_days, p_stars = p[0], p[1], p[2], p[3]
                p_target = p[10] if len(p) > 10 and p[10] else (p[9] if len(p) > 9 and isinstance(p[9], str) and p[9].startswith("http") else "")
                target_str = f" | 🔗 <code>{p_target}</code>" if p_target else ""
                link = f"https://t.me/{bot_info.username}?start=chanplan_{p_id}_{channel_id}"
                plans_list_text += f"\n• <b>{p_name}:</b> {p_days}d — {p_stars} ⭐{target_str}\n  └ <code>{link}</code>\n"
                del_label = f"🗑️ Borrar {p_name[:12]}" if lang == "es" else f"🗑️ Delete {p_name[:12]}"
                kb_rows.append([InlineKeyboardButton(text=del_label, callback_data=f"chplans_del_{p_id}_{channel_id}_{lang}")])
        else:
            plans_list_text = "\n<i>(No hay planes activos configurados aún)</i>" if lang == "es" else "\n<i>(No active plans configured yet)</i>"

        text = (
            f"💎 <b>Gestión de Membresías — {c_name}</b>\n\n"
            f"• <b>Suscriptores Activos:</b> <code>{sub_count}</code>\n"
            f"• <b>Planes de Suscripción:</b>\n{plans_list_text}\n"
            f"🛡️ <i>Cloud Media Management</i>"
        ) if lang == "es" else (
            f"💎 <b>Membership Management — {c_name}</b>\n\n"
            f"• <b>Active Subscribers:</b> <code>{sub_count}</code>\n"
            f"• <b>Subscription Plans:</b>\n{plans_list_text}\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )

        add_btn_text = "➕ Crear Nuevo Plan" if lang == "es" else "➕ Create New Plan"
        kb_rows.append([InlineKeyboardButton(text=add_btn_text, callback_data=f"chplans_add_{channel_id}_{lang}")])
        kb_rows.append([InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")])

        try:
            await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")
        except TelegramBadRequest:
            pass

    elif sub == "add":
        tier = await get_effective_group_tier(channel_id, callback.from_user.id)
        limit = CHAN_PLAN_TIER_LIMITS.get(tier, CHAN_PLAN_TIER_LIMITS["free"])
        active_plans = await get_channel_plans(channel_id, only_active=True)

        if len(active_plans) >= limit:
            limit_text = (
                f"🔒 <b>Límite de Planes Alcanzado</b>\n\n"
                f"Tu licencia actual (<b>{tier.upper()}</b>) permite un máximo de <b>{limit}</b> plan(es) de membresía activos, "
                f"y ya tienes <b>{len(active_plans)}</b> configurado(s).\n\n"
                f"Elimina un plan existente o mejora tu licencia para desbloquear más cupos.\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            ) if lang == "es" else (
                f"🔒 <b>Plan Limit Reached</b>\n\n"
                f"Your current license (<b>{tier.upper()}</b>) allows a maximum of <b>{limit}</b> active membership plan(s), "
                f"and you already have <b>{len(active_plans)}</b> configured.\n\n"
                f"Delete an existing plan or upgrade your license to unlock more slots.\n\n"
                f"🛡️ <i>Cloud Media Management</i>"
            )
            limit_kb_rows = []
            if tier == "free":
                up_label = "⭐ Mejorar a PRO (3 Planes)" if lang == "es" else "⭐ Upgrade to PRO (3 Plans)"
                limit_kb_rows.append([InlineKeyboardButton(text=up_label, callback_data=f"pay_pro_{channel_id}_{lang}")])
            if tier in ("free", "pro"):
                up_label_ultra = "💎 Mejorar a ULTRA PRO (10 Planes)" if lang == "es" else "💎 Upgrade to ULTRA PRO (10 Plans)"
                limit_kb_rows.append([InlineKeyboardButton(text=up_label_ultra, callback_data=f"pay_ultra_{channel_id}_{lang}")])
            limit_kb_rows.append([InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"chplans_menu_{channel_id}_{lang}")])

            try:
                await callback.message.edit_text(limit_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=limit_kb_rows), parse_mode="HTML")
            except TelegramBadRequest:
                await callback.message.answer(limit_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=limit_kb_rows), parse_mode="HTML")
            return

        CHAN_PLAN_STATES[(bot.id, callback.from_user.id)] = {"channel_id": channel_id, "step": "name", "lang": lang}
        prompt_name = (
            "✍️ <b>Nombre del nuevo plan:</b>\n\n"
            "Envía en este chat el nombre comercial de la suscripción (ejemplo: <code>Pase Mensual VIP</code>):"
        ) if lang == "es" else (
            "✍️ <b>New plan name:</b>\n\nSend the plan title (e.g. <code>Monthly VIP Pass</code>):"
        )
        prompt = await callback.message.answer(prompt_name, parse_mode="HTML")
        fire_and_forget_auto_delete([prompt], delay=60)

    elif sub == "skip":
        st_data = CHAN_PLAN_STATES.pop((bot.id, callback.from_user.id), None)
        if not st_data or st_data.get("channel_id") != channel_id or st_data.get("step") != "media":
            info_text = "ℹ️ No hay una creación de plan en curso para omitir." if lang == "es" else "ℹ️ There's no plan creation in progress to skip."
            info_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")]
            ])
            try:
                await callback.message.edit_text(info_text, reply_markup=info_kb, parse_mode="HTML")
            except TelegramBadRequest:
                pass
            return

        try:
            await callback.message.delete()
        except Exception:
            pass

        await _finalize_and_preview_channel_plan(
            bot=bot,
            chat_id=callback.message.chat.id,
            channel_id=channel_id,
            lang=lang,
            name=st_data["name"],
            days=st_data["days"],
            price=st_data["price"],
            promo_text=st_data.get("promo", ""),
            media_id=None,
            media_type=None,
            target_link=st_data.get("target_link")
        )

    elif sub == "del":
        await delete_channel_plan(plan_id)
        plans = await get_channel_plans(channel_id, only_active=True)
        sub_count = await get_active_subscribers_count(channel_id)
        bot_info = await bot.get_me()

        try:
            c_name = (await bot.get_chat(channel_id)).title
        except Exception:
            c_name = "Canal"

        plans_list_text = ""
        kb_rows = []
        if plans:
            for p in plans:
                p_id, p_name, p_days, p_stars = p[0], p[1], p[2], p[3]
                p_target = p[10] if len(p) > 10 and p[10] else (p[9] if len(p) > 9 and isinstance(p[9], str) and p[9].startswith("http") else "")
                target_str = f" | 🔗 <code>{p_target}</code>" if p_target else ""
                link = f"https://t.me/{bot_info.username}?start=chanplan_{p_id}_{channel_id}"
                plans_list_text += f"\n• <b>{p_name}:</b> {p_days}d — {p_stars} ⭐{target_str}\n  └ <code>{link}</code>\n"
                del_label = f"🗑️ Borrar {p_name[:12]}" if lang == "es" else f"🗑️ Delete {p_name[:12]}"
                kb_rows.append([InlineKeyboardButton(text=del_label, callback_data=f"chplans_del_{p_id}_{channel_id}_{lang}")])
        else:
            plans_list_text = "\n<i>(No hay planes activos configurados aún)</i>" if lang == "es" else "\n<i>(No active plans configured yet)</i>"

        text = (
            f"💎 <b>Gestión de Membresías — {c_name}</b>\n\n"
            f"• <b>Suscriptores Activos:</b> <code>{sub_count}</code>\n"
            f"• <b>Planes de Suscripción:</b>\n{plans_list_text}\n"
            f"🛡️ <i>Cloud Media Management</i>"
        ) if lang == "es" else (
            f"💎 <b>Membership Management — {c_name}</b>\n\n"
            f"• <b>Active Subscribers:</b> <code>{sub_count}</code>\n"
            f"• <b>Subscription Plans:</b>\n{plans_list_text}\n"
            f"🛡️ <i>Cloud Media Management</i>"
        )

        add_btn_text = "➕ Crear Nuevo Plan" if lang == "es" else "➕ Create New Plan"
        kb_rows.append([InlineKeyboardButton(text=add_btn_text, callback_data=f"chplans_add_{channel_id}_{lang}")])
        kb_rows.append([InlineKeyboardButton(text=t["btn_back_channel"], callback_data=f"cpanel_{channel_id}_{lang}")])

        try:
            await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")
        except TelegramBadRequest:
            pass