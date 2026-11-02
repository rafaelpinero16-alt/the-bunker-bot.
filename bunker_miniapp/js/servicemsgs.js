/**
 * servicemsgs.js — The Bunker OS · Mini App · v44 · Fase 9.1
 *
 * v44: invitaciones al videochat e impulsos como mensajes nativos configurables; aviso «📢 Aviso» (advise) del bot;
 *      botón propio por aviso (texto + enlace, PRO) e indicador / borrado de la multimedia que se fija desde el bot;
 *      doble lista negra con configuración (activar, acción ban/kick/mute/warn/advise, duración y borrado por lista)
 *      y lista blanca (ULTRA PRO). Mismos endpoints: /service-messages y /blacklists.
 *
 * Módulo ES autocontenido que pinta #svc-panel en la consola de la comunidad, con tres pestañas:
 *   • 📨 Mensajes: reglas de los mensajes nativos de Telegram (entradas, salidas, videochat, fijados, título / foto:
 *     mantener · borrar ya · borrar a los X s), autoborrado de los avisos del bot (warn / mute / kick / ban), texto
 *     propio y botón «Apelar» (PRO) y horario de emisión de los mensajes del Video Chat (PRO).
 *   • 🚫 Doble Lista Negra (ULTRA PRO): lista de baneo inmediato y lista de advertencia + borrado.
 *   • 💰 Donaciones: enlaces externos (PRO) que aparecen junto a los montos en Stars de las propinas.
 *
 * La licencia de cada parte la decide el servidor (402 si no alcanza); la interfaz solo la refleja con el bloque
 * `features` que devuelve cada GET y ofrece la activación en el bot. Mismas garantías que auditlog.js: api.request
 * (credencial única, epoch, { __error } sin lanzar), ids validados, DOM con textContent y respuestas obsoletas
 * descartadas al cambiar de comunidad o de sesión.
 * The Bunker Command OS © 2026 — Cloud Media Management
 */
import { api, session } from './api.js';
import { ui } from './ui.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';
import { CONFIG } from './config.js';

const TIMEOUT_MS = CONFIG.SERVICE_MSGS?.TIMEOUT_MS || 12000;
const KINDS = ['join', 'leave', 'videochat', 'vc_invite', 'boost', 'pinned', 'chat_info'];
const NOTICE_KINDS = ['warn', 'mute', 'kick', 'ban', 'advise'];
const BL_ACTIONS = ['ban', 'kick', 'mute', 'warn', 'advise'];
const BL_DURATIONS = [0, 60, 1440, 10080, 43200];   // minutos · 0 = permanente
const DELAYS = CONFIG.SERVICE_MSGS?.DELAYS || [10, 30, 60, 300, 900, 3600];
const NOTICE_TTLS = CONFIG.SERVICE_MSGS?.NOTICE_TTLS || [null, 10, 30, 60, 300, 0, -1];   // v45 · -1 = no publicar
const VC_INTERVALS = CONFIG.SERVICE_MSGS?.VC_INTERVALS || [0, 30, 60, 120, 300, 600];
const LINKS_MAX = 5;

const TEXTS = {
    en: {
        title: 'Service Messages', desc: 'Telegram notices, bot notices, voice-chat schedule, double blacklist and donation links.',
        pick: 'Select a community.', loading: 'Loading…', retry: 'Retry', error_generic: 'Something went wrong.',
        err_forbidden: 'Only the community owner (or an admin who can add admins) can change this.',
        err_network: 'Connection problem. Try again.', err_missing: 'This server does not have this API yet (update the backend).',
        tab_messages: '📨 Messages', tab_blacklist: '🚫 Blacklist', tab_donations: '💰 Donations',
        saved: 'Saved', save: 'Save',
        native_title: 'Telegram service messages', native_hint: 'What to do with Telegram\'s automatic notices in this community.',
        k_join: '👋 Joins', k_leave: '🚪 Leaves', k_videochat: '🎙️ Video chat started / ended', k_vc_invite: '📨 Video chat invitations',
        k_boost: '🚀 Boosts', k_pinned: '📌 Pins', k_chat_info: '🖼️ Title / photo',
        a_keep: 'Keep', a_delete: 'Delete now', a_delay: 'Delete after…',
        notices_title: 'Bot sanction notices', notices_hint: 'For each notice: 🟢 Keep (stays in the chat), 🔴 Don\'t post (silent sanction) or ⏳ auto-delete after a time («Default» = the group\'s service TTL). FREE: official text · PRO: your own text + ⚖️ Appeal · ULTRA PRO: plus media and your own button.',
        n_warn: '⚠️ Warning', n_mute: '🔇 Mute', n_kick: '👢 Kick', n_ban: '🚫 Ban', n_advise: '📢 Notice',
        ttl_default: '⏳ Default', ttl_pinned: '🟢 Keep', ttl_off: '🔴 Don\'t post', ttl_after: '⏳ {d}',
        media_ultra: '💎 Media and your own button under each notice are ULTRA PRO.',
        acc_title: '👥 Who can use moderation commands', acc_hint: '/ban /kick /mute /warn /unban /unmute /unwarn /whitelist /blacklist. Only the Owner can change this. In «Owner only» mode anonymous admins are blocked too.',
        acc_admins: '🛡️ Admins with «Restrict members»', acc_owner: '👑 Owner only',
        bl_basic_title: '📛 Basic blacklist 🆓', bl_basic_hint: 'Up to {max} words. Whoever writes one loses the message and gets a warning (the ⚠️ Warnings ladder decides the final sanction). From the group: /blacklist word.',
        bl_basic_list: '⚠️ Banned words (delete + warn)', bl_basic_upsell: '💎 ULTRA PRO turns it into the Double Blacklist: a ban list and a warn list of 200 words each, an action per list with duration and deletion, and an allowlist.',
        btn_title: 'Button under the notice', btn_label: 'Button text', btn_url: 'https://… or t.me/…', btn_clear: 'Remove button',
        btn_hint: 'For example «📜 Rules». The ⚖️ Appeal button is added separately.',
        media_on: '🖼️ Media set from the bot', media_clear: 'Remove media', media_hint: 'To add a photo, video or GIF: bot → Group Settings → 📨 Service messages → 🤖 Bot notices → ✍️.',
        custom_text: 'Custom text (HTML)', custom_hint: 'Variables: {mention} {strikes} {limit} {reason} {action}. Empty = official text.',
        appeal: '⚖️ «Appeal» button on automatic bans', appeal_hint: 'Requires ban appeals to be enabled for this community.',
        vc_title: 'Voice-chat message schedule', vc_hint: 'Outside these hours (or with posting off) the Sentinel and the bot post nothing in the chat; acoustic moderation keeps working silently.',
        vc_enabled: 'Posting', vc_all_day: 'All day', vc_start: 'From', vc_end: 'To', vc_interval: 'Minimum gap between member notices',
        vc_no_limit: 'No limit', vc_both: 'Set both times or choose «All day».',
        bl_title: 'Double Blacklist', bl_hint: 'Terms of your own (case-insensitive, whole words). A term lives in only one list. For each list choose the action, the duration (ban / mute) and whether the message is deleted. The allowlist always wins.',
        bl_ban: '⛔ Ban list (default: ban on sight)', bl_warn: '⚠️ Warn list (default: delete + warn)',
        bl_placeholder: 'term, another term…', bl_add: 'Add', bl_empty: 'Empty', bl_remove: 'Remove',
        bl_added: '{a} added · {m} moved · {s} already listed',
        bl_on: 'Blacklists on', bl_rule: 'When a term is detected', bl_action: 'Action', bl_duration: 'Duration', bl_delete: 'Delete the message',
        act_ban: '⛔ Ban', act_kick: '👢 Kick', act_mute: '🔇 Mute', act_warn: '⚠️ Warn', act_advise: '📢 Notice only',
        dur_0: 'Permanent', dur_60: '1 h', dur_1440: '1 day', dur_10080: '7 days', dur_43200: '30 days',
        bl_allow: '✅ Allowlist — never counts (not even in the global list)', bl_allow_added: '{a} added to the allowlist',
        don_title: 'External donation links', don_hint: 'Shown next to the Stars amounts when a member opens your tips. Amounts and texts are in the Tips section above.',
        don_label: 'Name', don_url: 'https://… or t.me/…', don_add: 'Add link', don_empty: 'No links yet.',
        lock_pro: 'Requires a PRO ⭐ license', lock_ultra: 'Requires an ULTRA PRO 💎 license', get_pro: '⭐ Get PRO', get_ultra: '💎 Get ULTRA PRO',
        s: 's', min: 'min', h: 'h',
    },
    es: {
        title: 'Mensajes de Servicio', desc: 'Avisos de Telegram, avisos del bot, horario del videochat, doble lista negra y enlaces de donación.',
        pick: 'Selecciona una comunidad.', loading: 'Cargando…', retry: 'Reintentar', error_generic: 'Algo salió mal.',
        err_forbidden: 'Solo el Dueño de la comunidad (o un admin que puede añadir administradores) puede cambiar esto.',
        err_network: 'Problema de conexión. Inténtalo de nuevo.', err_missing: 'Este servidor aún no tiene esta API (actualiza el backend).',
        tab_messages: '📨 Mensajes', tab_blacklist: '🚫 Lista negra', tab_donations: '💰 Donaciones',
        saved: 'Guardado', save: 'Guardar',
        native_title: 'Mensajes de servicio de Telegram', native_hint: 'Qué hacer con los avisos automáticos de Telegram en esta comunidad.',
        k_join: '👋 Entradas', k_leave: '🚪 Salidas', k_videochat: '🎙️ Videochat abierto / cerrado', k_vc_invite: '📨 Invitaciones al videochat',
        k_boost: '🚀 Impulsos', k_pinned: '📌 Fijados', k_chat_info: '🖼️ Título / foto',
        a_keep: 'Mantener', a_delete: 'Borrar ya', a_delay: 'Borrar a los…',
        notices_title: 'Avisos de sanción del bot', notices_hint: 'Para cada aviso: 🟢 Conservar (se queda en el chat), 🔴 No publicar (sanción en silencio) o ⏳ auto-eliminar tras un tiempo («Por defecto» = TTL de servicio del grupo). FREE: texto oficial · PRO: texto propio + ⚖️ Apelar · ULTRA PRO: además multimedia y botón propio.',
        n_warn: '⚠️ Advertencia', n_mute: '🔇 Silencio', n_kick: '👢 Expulsión', n_ban: '🚫 Baneo', n_advise: '📢 Aviso',
        ttl_default: '⏳ Por defecto', ttl_pinned: '🟢 Conservar', ttl_off: '🔴 No publicar', ttl_after: '⏳ {d}',
        media_ultra: '💎 La multimedia y el botón propio bajo cada aviso son ULTRA PRO.',
        acc_title: '👥 Quién puede usar los comandos de moderación', acc_hint: '/ban /kick /mute /warn /unban /unmute /unwarn /whitelist /blacklist. Solo el Dueño puede cambiarlo. En modo «Solo el Dueño» también se bloquean los admins anónimos.',
        acc_admins: '🛡️ Admins con «Restringir miembros»', acc_owner: '👑 Solo el Dueño',
        bl_basic_title: '📛 Lista negra básica 🆓', bl_basic_hint: 'Hasta {max} palabras. Quien escribe una pierde el mensaje y recibe una advertencia (la escalera de ⚠️ Advertencias decide la sanción final). Desde el grupo: /blacklist palabra.',
        bl_basic_list: '⚠️ Palabras prohibidas (borrar + advertir)', bl_basic_upsell: '💎 ULTRA PRO la convierte en la Doble Lista Negra: una lista de baneo y otra de advertencia de 200 palabras cada una, una acción por lista con duración y borrado, y lista blanca.',
        btn_title: 'Botón bajo el aviso', btn_label: 'Texto del botón', btn_url: 'https://… o t.me/…', btn_clear: 'Quitar botón',
        btn_hint: 'Por ejemplo «📜 Normas». El botón ⚖️ Apelar se añade aparte.',
        media_on: '🖼️ Multimedia fijada desde el bot', media_clear: 'Quitar multimedia', media_hint: 'Para añadir foto, vídeo o GIF: bot → Configuración de grupos → 📨 Mensajes de servicio → 🤖 Avisos del bot → ✍️.',
        custom_text: 'Texto propio (HTML)', custom_hint: 'Variables: {mention} {strikes} {limit} {reason} {action}. Vacío = texto oficial.',
        appeal: '⚖️ Botón «Apelar» en baneos automáticos', appeal_hint: 'Requiere tener activas las apelaciones de baneo en esta comunidad.',
        vc_title: 'Horario de los mensajes del videochat', vc_hint: 'Fuera de este horario (o con la emisión apagada) el Centinela y el bot no publican nada en el chat; la moderación acústica sigue en silencio.',
        vc_enabled: 'Emisión', vc_all_day: 'Todo el día', vc_start: 'Desde', vc_end: 'Hasta', vc_interval: 'Separación mínima entre avisos de miembros',
        vc_no_limit: 'Sin límite', vc_both: 'Indica las dos horas o elige «Todo el día».',
        bl_title: 'Doble Lista Negra', bl_hint: 'Términos propios (sin distinguir mayúsculas, palabra completa). Un término solo está en una lista. En cada lista eliges la acción, la duración (ban / mute) y si se borra el mensaje. La lista blanca gana siempre.',
        bl_ban: '⛔ Lista de baneo (por defecto: banea en el acto)', bl_warn: '⚠️ Lista de advertencia (por defecto: borra + advierte)',
        bl_placeholder: 'término, otro término…', bl_add: 'Añadir', bl_empty: 'Vacía', bl_remove: 'Quitar',
        bl_added: '{a} añadidos · {m} movidos · {s} ya estaban',
        bl_on: 'Listas negras activas', bl_rule: 'Al detectar un término', bl_action: 'Acción', bl_duration: 'Duración', bl_delete: 'Borrar el mensaje',
        act_ban: '⛔ Banear', act_kick: '👢 Expulsar', act_mute: '🔇 Silenciar', act_warn: '⚠️ Advertir', act_advise: '📢 Solo aviso',
        dur_0: 'Permanente', dur_60: '1 h', dur_1440: '1 día', dur_10080: '7 días', dur_43200: '30 días',
        bl_allow: '✅ Lista blanca — nunca cuenta (ni en la lista global)', bl_allow_added: '{a} añadidos a la lista blanca',
        don_title: 'Enlaces externos de donación', don_hint: 'Aparecen junto a los montos en Stars cuando un miembro abre tus propinas. Los montos y textos están en la sección Propinas de arriba.',
        don_label: 'Nombre', don_url: 'https://… o t.me/…', don_add: 'Añadir enlace', don_empty: 'Aún no hay enlaces.',
        lock_pro: 'Requiere licencia PRO ⭐', lock_ultra: 'Requiere licencia ULTRA PRO 💎', get_pro: '⭐ Activar PRO', get_ultra: '💎 Activar ULTRA PRO',
        s: 's', min: 'min', h: 'h',
    },
};

function lang() { return state.currentLang === 'es' ? 'es' : 'en'; }
function t(key, vars = {}) {
    const text = (TEXTS[lang()] && TEXTS[lang()][key]) || TEXTS.en[key] || key;
    return text.replace(/\{(\w+)\}/g, (_, name) => (vars[name] !== undefined ? String(vars[name]) : `{${name}}`));
}

// ==========================================================================
// 🔌 SERVICIO DE API
// ==========================================================================
const INVALID_ID = Object.freeze({ __error: 'invalid_id', __status: 400 });
function chatIdPart(value) {
    const text = String(value ?? '').trim();
    return /^-\d{5,20}$/.test(text) ? text : null;
}

export const serviceMessagesService = {
    /** GET /api/chat/{id}/service-messages → { service_messages: { native, bot_notices, vc_messages, donation_links, options }, features } */
    async fetchBundle(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/service-messages?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },
    /** POST { native?, bot_notices?, vc_messages? } — todo o nada (422 "campo: motivo"), 402 por parte de pago. */
    async saveBundle(chatId, patch) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.post(`/chat/${chat}/service-messages`, { service_messages: patch }, { timeoutMs: TIMEOUT_MS });
    },
    /** GET /api/chat/{id}/blacklists → { blacklists: { ban[], warn[] }, options, features } */
    async fetchBlacklists(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/blacklists?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },
    /** POST { action: 'add' | 'remove', list_type?, terms[] } — ULTRA PRO (402). */
    async updateBlacklists(chatId, body) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.post(`/chat/${chat}/blacklists`, body, { timeoutMs: TIMEOUT_MS });
    },
    /** GET /api/chat/{id}/donations → { donations: { links[] }, options, features } */
    async fetchDonations(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/donations?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },
    /** POST { donations: { links: [{label, url}] } } — lista completa, PRO (402). */
    async saveDonations(chatId, links) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.post(`/chat/${chat}/donations`, { donations: { links } }, { timeoutMs: TIMEOUT_MS });
    },
};

/** Términos desde texto libre (comas o saltos de línea), sin vacíos ni duplicados. */
export function parseTerms(raw) {
    const out = [];
    String(raw || '').split(/[\n,]+/).map(s => s.trim().toLowerCase().replace(/\s+/g, ' ')).forEach(term => {
        if (term && !out.includes(term)) out.push(term);
    });
    return out;
}

// ==========================================================================
// 🧱 DOM SEGURO
// ==========================================================================
function h(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
        if (value === undefined || value === null || value === false) return;
        if (key === 'class') el.className = value;
        else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
        else if (value === true) el.setAttribute(key, '');
        else el.setAttribute(key, String(value));
    });
    (Array.isArray(children) ? children : [children]).forEach(child => {
        if (child === null || child === undefined || child === false) return;
        el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return el;
}

const CLS = {
    btn: 'px-2.5 py-1.5 rounded-lg border text-[10px] font-extrabold uppercase active:scale-95 transition disabled:opacity-50',
    chip: 'py-1.5 rounded-lg border text-[10px] font-extrabold transition',
    input: 'w-full bg-black/60 border border-neutral-700 rounded-lg px-2.5 py-1.5 text-[11px] text-theme-main focus:outline-none focus:border-cyan-400/60',
    select: 'bg-black/60 border border-neutral-700 rounded-lg px-2 py-1 text-[10px] text-theme-main focus:outline-none',
    label: 'block text-[9px] font-bold text-theme-muted uppercase tracking-wide',
    card: 'rounded-xl border border-neutral-800 bg-black/30 p-2.5 space-y-2',
    muted: 'text-[10px] text-neutral-500 leading-snug',
};
const ON_CLS = 'border-cyan-400/60 bg-cyan-400/10 text-cyan-300';
const OFF_CLS = 'border-neutral-800 bg-black/40 text-neutral-400';

function fmtSecs(seconds) {
    const s = Number(seconds) || 0;
    if (s >= 3600 && s % 3600 === 0) return `${s / 3600} ${t('h')}`;
    if (s >= 60 && s % 60 === 0) return `${s / 60} ${t('min')}`;
    return `${s} ${t('s')}`;
}

function errorText(res) {
    if (!res) return t('error_generic');
    if (res.__status === 403) return t('err_forbidden');
    if (res.__status === 402) return res.__paywall?.required_tier === 'ultra_pro' ? t('lock_ultra') : t('lock_pro');
    if (res.__error === 'network' || res.__error === 'timeout') return t('err_network');
    if (api.isEndpointMissing && api.isEndpointMissing(res)) return t('err_missing');
    return String(res.__error || t('error_generic'));
}

function toast(tone, icon, title, body = '') {
    ui.showToast({ key: 'svc-msgs', force: true, icon, tone, ttl: 3600, title, body });
}

function haptic(kind) {
    try {
        if (kind === 'ok') tgApp.hapticNotification('success');
        else if (kind === 'error') tgApp.hapticNotification('error');
        else tgApp.hapticImpact('light');
    } catch (e) { /* fuera de Telegram */ }
}

function select(options, current, onchange, disabled = false) {
    const el = h('select', { class: CLS.select, disabled, onchange: ev => onchange(ev.target.value) },
        options.map(([value, label]) => {
            const opt = h('option', { value: value === null ? '' : String(value) }, label);
            if (String(value ?? '') === String(current ?? '')) opt.selected = true;
            return opt;
        }));
    return el;
}

// ==========================================================================
// 🧭 CONSOLA
// ==========================================================================
class ServiceMessagesConsole {
    constructor() {
        this.chatId = null;
        this.seq = 0;
        this.tab = 'messages';
        this.reset();
    }

    reset() {
        this.msgs = { data: null, features: null, error: '', busy: false, drafts: {} };
        this.bl = { data: null, config: null, allowlist: null, features: null, error: '', busy: false, input: { ban: '', warn: '', allow: '' } };
        this.don = { links: null, features: null, error: '', busy: false, label: '', url: '' };
    }

    get root() { return document.getElementById('svc-root'); }

    guard() {
        const ctx = { chatId: this.chatId, seq: this.seq, epoch: session.epoch };
        return res => !(res && res.__stale) && ctx.chatId === this.chatId && ctx.seq === this.seq && ctx.epoch === session.epoch;
    }

    start(chatId) {
        this.stop();
        this.chatId = String(chatId);
        this.seq += 1;
        this.render();
        this.loadTab();
    }

    stop() {
        this.chatId = null;
        this.seq += 1;
        this.reset();
        this.render();
    }

    /** Señal del radar (settings_updated con svcmsg_* / blk_* / don_*): recarga solo lo que cambió. */
    onRemoteChange(changed = []) {
        if (!this.chatId) return;
        const keys = (Array.isArray(changed) ? changed : []).map(String);
        if (keys.some(k => k.startsWith('svcmsg_') || k === 'backup_restored')) { this.msgs.data = null; if (this.tab === 'messages') this.loadMessages(); }
        if (keys.some(k => k.startsWith('blk_') || k === 'backup_restored')) { this.bl.data = null; if (this.tab === 'blacklist') this.loadBlacklists(); }
        if (keys.some(k => k.startsWith('don_') || k === 'backup_restored')) { this.don.links = null; if (this.tab === 'donations') this.loadDonations(); }
    }

    setTab(tab) {
        if (this.tab === tab) return;
        haptic();
        this.tab = tab;
        this.render();
        this.loadTab();
    }

    loadTab() {
        if (!this.chatId) return;
        if (this.tab === 'messages' && !this.msgs.data) this.loadMessages();
        if (this.tab === 'blacklist' && !this.bl.data) this.loadBlacklists();
        if (this.tab === 'donations' && !this.don.links) this.loadDonations();
    }

    // ---------------------------------------------------------------- Mensajes
    async loadMessages() {
        const ok = this.guard();
        this.msgs.error = '';
        this.render();
        const res = await serviceMessagesService.fetchBundle(this.chatId);
        if (!ok(res)) return;
        if (res.__error) this.msgs.error = errorText(res);
        else Object.assign(this.msgs, { data: res.service_messages, features: res.features || {}, drafts: {} });
        this.render();
    }

    async saveMessages(patch, okText = t('saved')) {
        if (!this.chatId || this.msgs.busy) return false;
        const ok = this.guard();
        this.msgs.busy = true;
        this.render();
        const res = await serviceMessagesService.saveBundle(this.chatId, patch);
        if (!ok(res)) return false;
        this.msgs.busy = false;
        if (res.__error) {
            haptic('error');
            toast('error', '⚠️', errorText(res));
            this.render();
            return false;
        }
        haptic('ok');
        Object.assign(this.msgs, { data: res.service_messages, features: res.features || this.msgs.features, drafts: {} });
        toast('success', '📨', okText);
        this.render();
        return true;
    }

    lockBox(tier) {
        const ultra = tier === 'ultra_pro';
        const pay = () => { if (window.app?.pagarPlan) window.app.pagarPlan(ultra ? 'ultra' : 'pro', this.chatId); };
        return h('div', { class: 'flex items-center justify-between gap-2 rounded-lg border border-amber-400/30 bg-amber-400/5 px-2.5 py-1.5' }, [
            h('span', { class: 'text-[10px] font-bold text-amber-300' }, `🔒 ${ultra ? t('lock_ultra') : t('lock_pro')}`),
            h('button', { type: 'button', class: `${CLS.btn} border-amber-400/40 bg-amber-400/10 text-amber-300 shrink-0`, onclick: pay },
                ultra ? t('get_ultra') : t('get_pro')),
        ]);
    }

    renderNative(native, busy) {
        const rows = KINDS.map(kind => {
            const rule = native[kind] || { action: 'keep', delay: 30 };
            const actionSel = select([['keep', t('a_keep')], ['delete', t('a_delete')], ['delay', t('a_delay')]], rule.action,
                value => this.saveMessages({ native: { [kind]: { action: value } } }), busy);
            const delaySel = rule.action === 'delay'
                ? select([...new Set([...DELAYS, rule.delay])].sort((a, b) => a - b).map(d => [d, fmtSecs(d)]), rule.delay,
                    value => this.saveMessages({ native: { [kind]: { delay: Number(value) } } }), busy)
                : null;
            return h('div', { class: 'flex items-center justify-between gap-2' }, [
                h('span', { class: 'text-[11px] font-bold text-theme-main' }, t(`k_${kind}`)),
                h('div', { class: 'flex items-center gap-1.5 shrink-0' }, [actionSel, delaySel]),
            ]);
        });
        return h('div', { class: CLS.card }, [
            h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, t('native_title')),
            h('p', { class: CLS.muted }, t('native_hint')),
            ...rows,
        ]);
    }

    renderNotices(notices, busy, paid, ultra = false) {
        const items = notices.notices || {};
        const ttlLabel = v => (v === null ? t('ttl_default') : v === 0 ? t('ttl_pinned') : v < 0 ? t('ttl_off') : t('ttl_after', { d: fmtSecs(v) }));
        const rows = NOTICE_KINDS.map(kind => {
            const item = items[kind] || {};
            const ttlSel = select(NOTICE_TTLS.map(v => [v, ttlLabel(v)]),
                item.ttl, value => this.saveMessages({ bot_notices: { notices: { [kind]: { ttl: value === '' ? null : Number(value) } } } }), busy);
            const block = [h('div', { class: 'flex items-center justify-between gap-2' }, [
                h('span', { class: 'text-[11px] font-bold text-theme-main' }, t(`n_${kind}`)), ttlSel])];
            if (paid) {
                const draftKey = `text_${kind}`;
                const area = h('textarea', {
                    class: `${CLS.input} font-mono h-16 resize-y`, maxlength: '900', spellcheck: 'false', disabled: busy,
                    placeholder: t('custom_text'), oninput: ev => { this.msgs.drafts[draftKey] = ev.target.value; },
                });
                area.value = this.msgs.drafts[draftKey] ?? (item.text || '');
                block.push(h('details', { class: 'group' }, [
                    h('summary', { class: 'text-[9px] text-neutral-400 cursor-pointer select-none' }, `✍️ ${t('custom_text')}${item.text ? ' ●' : ''}`),
                    h('div', { class: 'space-y-1 pt-1' }, [area, h('p', { class: CLS.muted }, t('custom_hint')),
                        h('button', { type: 'button', class: `${CLS.btn} border-cyan-400/40 bg-cyan-400/10 text-cyan-300`, disabled: busy,
                            onclick: () => this.saveMessages({ bot_notices: { notices: { [kind]: { text: (area.value || '').trim() || null } } } }) },
                        t('save')),
                        !ultra ? h('p', { class: CLS.muted }, t('media_ultra')) : item.media_id
                            ? h('div', { class: 'flex items-center justify-between gap-2' }, [
                                h('span', { class: 'text-[9px] font-bold text-emerald-300' }, t('media_on')),
                                h('button', { type: 'button', class: `${CLS.btn} border-red-400/40 bg-red-400/10 text-red-300 shrink-0`, disabled: busy,
                                    onclick: () => this.saveMessages({ bot_notices: { notices: { [kind]: { media_id: null, media_type: null } } } }) },
                                t('media_clear'))])
                            : h('p', { class: CLS.muted }, t('media_hint'))]),
                ]));
                const labelKey = `btnl_${kind}`, urlKey = `btnu_${kind}`;
                const bLabel = h('input', { type: 'text', class: CLS.input, maxlength: '40', placeholder: t('btn_label'), disabled: busy,
                    oninput: ev => { this.msgs.drafts[labelKey] = ev.target.value; } });
                bLabel.value = this.msgs.drafts[labelKey] ?? (item.button_text || '');
                const bUrl = h('input', { type: 'url', class: CLS.input, maxlength: '512', placeholder: t('btn_url'), disabled: busy,
                    autocomplete: 'off', spellcheck: 'false', oninput: ev => { this.msgs.drafts[urlKey] = ev.target.value; } });
                bUrl.value = this.msgs.drafts[urlKey] ?? (item.button_url || '');
                if (ultra) block.push(h('details', { class: 'group' }, [
                    h('summary', { class: 'text-[9px] text-neutral-400 cursor-pointer select-none' }, `🔘 ${t('btn_title')}${item.button_url ? ' ●' : ''}`),
                    h('div', { class: 'space-y-1 pt-1' }, [bLabel, bUrl, h('p', { class: CLS.muted }, t('btn_hint')),
                        h('div', { class: 'flex gap-1.5' }, [
                            h('button', { type: 'button', class: `${CLS.btn} border-cyan-400/40 bg-cyan-400/10 text-cyan-300`, disabled: busy,
                                onclick: () => {
                                    const label = (bLabel.value || '').trim(), url = (bUrl.value || '').trim();
                                    if (!label || !url) { toast('warn', '⚠️', `${t('btn_label')} + ${t('btn_url')}`); return; }
                                    delete this.msgs.drafts[labelKey]; delete this.msgs.drafts[urlKey];
                                    this.saveMessages({ bot_notices: { notices: { [kind]: { button_text: label, button_url: url } } } });
                                } }, t('save')),
                            item.button_url ? h('button', { type: 'button', class: `${CLS.btn} border-red-400/40 bg-red-400/10 text-red-300`, disabled: busy,
                                onclick: () => {
                                    delete this.msgs.drafts[labelKey]; delete this.msgs.drafts[urlKey];
                                    this.saveMessages({ bot_notices: { notices: { [kind]: { button_text: null, button_url: null } } } });
                                } }, t('btn_clear')) : null]),
                    ]),
                ]));
            }
            return h('div', { class: 'space-y-1' }, block);
        });
        const appealOn = Boolean(notices.appeal_button);
        const appeal = paid ? h('button', {
            type: 'button', class: `${CLS.chip} w-full ${appealOn ? ON_CLS : OFF_CLS}`, disabled: busy,
            'aria-pressed': appealOn ? 'true' : 'false',
            onclick: () => this.saveMessages({ bot_notices: { appeal_button: !appealOn } }),
        }, `${appealOn ? '🟢' : '⚪'} ${t('appeal')}`) : null;
        return h('div', { class: CLS.card }, [
            h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, t('notices_title')),
            h('p', { class: CLS.muted }, t('notices_hint')),
            ...rows,
            appeal, paid ? h('p', { class: CLS.muted }, t('appeal_hint')) : this.lockBox('pro'),
            paid && !ultra ? this.lockBox('ultra_pro') : null,
        ]);
    }

    renderCmdAccess(mode, busy) {
        const sel = select([['admins', t('acc_admins')], ['owner', t('acc_owner')]], mode || 'admins',
            value => this.saveMessages({ cmd_access: value }), busy);
        return h('div', { class: CLS.card }, [
            h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, t('acc_title')),
            h('p', { class: CLS.muted }, t('acc_hint')),
            h('div', { class: 'flex justify-end' }, [sel]),
        ]);
    }

    renderVc(vc, busy, paid) {
        if (!paid) {
            return h('div', { class: CLS.card }, [
                h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, t('vc_title')),
                h('p', { class: CLS.muted }, t('vc_hint')), this.lockBox('pro')]);
        }
        const allDay = !vc.start;
        const toggle = h('button', {
            type: 'button', class: `${CLS.chip} px-3 ${vc.enabled ? ON_CLS : OFF_CLS}`, disabled: busy, role: 'switch',
            'aria-checked': vc.enabled ? 'true' : 'false', onclick: () => this.saveMessages({ vc_messages: { enabled: !vc.enabled } }),
        }, `${vc.enabled ? '🟢' : '🔴'} ${t('vc_enabled')}`);
        const allDayBtn = h('button', {
            type: 'button', class: `${CLS.chip} px-3 ${allDay ? ON_CLS : OFF_CLS}`, disabled: busy || allDay,
            onclick: () => this.saveMessages({ vc_messages: { start: null, end: null } }),
        }, `♾ ${t('vc_all_day')}`);
        const start = h('input', { type: 'time', class: CLS.select, value: vc.start || '20:00', disabled: busy, step: '60' });
        const end = h('input', { type: 'time', class: CLS.select, value: vc.end || '23:00', disabled: busy, step: '60' });
        const apply = h('button', {
            type: 'button', class: `${CLS.btn} border-cyan-400/40 bg-cyan-400/10 text-cyan-300`, disabled: busy,
            onclick: () => {
                if (!start.value || !end.value) { toast('warn', '⚠️', t('vc_both')); return; }
                this.saveMessages({ vc_messages: { start: start.value.slice(0, 5), end: end.value.slice(0, 5) } });
            },
        }, t('save'));
        const interval = select(VC_INTERVALS.map(v => [v, v === 0 ? t('vc_no_limit') : fmtSecs(v)]), vc.interval,
            value => this.saveMessages({ vc_messages: { interval: Number(value) } }), busy);
        return h('div', { class: CLS.card }, [
            h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, t('vc_title')),
            h('p', { class: CLS.muted }, t('vc_hint')),
            h('div', { class: 'flex flex-wrap items-center gap-1.5' }, [toggle, allDayBtn]),
            h('div', { class: 'flex flex-wrap items-center gap-1.5' }, [
                h('span', { class: CLS.label }, t('vc_start')), start, h('span', { class: CLS.label }, t('vc_end')), end, apply]),
            h('div', { class: 'flex items-center justify-between gap-2' }, [h('span', { class: CLS.label }, t('vc_interval')), interval]),
        ]);
    }

    renderMessages() {
        const m = this.msgs;
        if (m.error) return this.errorBox(m.error, () => this.loadMessages());
        if (!m.data) return this.loadingBox();
        const f = m.features || {};
        return h('div', { class: 'space-y-2' }, [
            this.renderNative(m.data.native || {}, m.busy),
            this.renderNotices(m.data.bot_notices || {}, m.busy, Boolean(f.bot_notice_custom), Boolean(f.bot_notice_media)),
            this.renderVc(m.data.vc_messages || {}, m.busy, Boolean(f.vc_message_window)),
            this.renderCmdAccess(m.data.cmd_access, m.busy),
        ]);
    }

    // ---------------------------------------------------------------- Doble lista negra
    async loadBlacklists() {
        const ok = this.guard();
        this.bl.error = '';
        this.render();
        const res = await serviceMessagesService.fetchBlacklists(this.chatId);
        if (!ok(res)) return;
        if (res.__error) this.bl.error = errorText(res);
        else Object.assign(this.bl, { data: res.blacklists, config: res.config || null, allowlist: res.allowlist || [], features: res.features || {},
            basicMax: Number(res.options?.basic_max) || 20 });
        this.render();
    }

    async blAction(body, okText) {
        if (!this.chatId || this.bl.busy) return;
        const ok = this.guard();
        this.bl.busy = true;
        this.render();
        const res = await serviceMessagesService.updateBlacklists(this.chatId, body);
        if (!ok(res)) return;
        this.bl.busy = false;
        if (res.__error) {
            haptic('error');
            toast('error', '⚠️', errorText(res));
        } else {
            haptic('ok');
            this.bl.data = res.lists || this.bl.data;
            if (res.config) this.bl.config = res.config;
            if (Array.isArray(res.allowlist)) this.bl.allowlist = res.allowlist;
            if (body.action === 'add') this.bl.input[body.list_type] = '';
            toast('success', '🚫', okText(res));
        }
        this.render();
    }

    renderList(kind, terms, busy, basic = false) {
        const input = h('input', {
            type: 'text', class: `${CLS.input} flex-1 min-w-0`, maxlength: '600', autocomplete: 'off', spellcheck: 'false',
            placeholder: t('bl_placeholder'), value: this.bl.input[kind], disabled: busy,
            oninput: ev => { this.bl.input[kind] = ev.target.value; },
            onkeydown: ev => { if (ev.key === 'Enter') { ev.preventDefault(); add(); } },
        });
        const add = () => {
            const list = parseTerms(input.value);
            if (!list.length) return;
            this.blAction({ action: 'add', list_type: kind, terms: list },
                res => (kind === 'allow' ? t('bl_allow_added', { a: res.added ?? 0 })
                    : t('bl_added', { a: res.added ?? 0, m: res.moved ?? 0, s: res.skipped ?? 0 })));
        };
        const chips = terms.length ? terms.map(term => h('span', {
            class: 'inline-flex items-center gap-1 rounded-full border border-neutral-700 bg-black/50 px-2 py-0.5 text-[10px] font-mono text-theme-main',
        }, [term, h('button', {
            type: 'button', class: 'text-neutral-500 hover:text-red-300 disabled:opacity-40', disabled: busy, 'aria-label': `${t('bl_remove')} ${term}`,
            onclick: () => this.blAction(kind === 'allow' ? { action: 'remove', list_type: 'allow', terms: [term] }
                : { action: 'remove', terms: [term] }, () => t('saved')),
        }, '×')])) : [h('span', { class: CLS.muted }, t('bl_empty'))];
        const color = kind === 'ban' ? 'text-red-300' : (kind === 'allow' ? 'text-emerald-300' : 'text-amber-300');
        const title = basic ? t('bl_basic_list') : t(kind === 'ban' ? 'bl_ban' : (kind === 'allow' ? 'bl_allow' : 'bl_warn'));
        return h('div', { class: CLS.card }, [
            h('p', { class: `text-[11px] font-extrabold ${color}` }, `${title} (${terms.length}${basic ? `/${this.bl.basicMax || 20}` : ''})`),
            kind === 'allow' || basic ? null : this.renderRule(kind, busy),
            h('div', { class: 'flex gap-1.5' }, [input, h('button', {
                type: 'button', class: `${CLS.btn} border-cyan-400/40 bg-cyan-400/10 text-cyan-300 shrink-0`, disabled: busy, onclick: add,
            }, busy ? '…' : t('bl_add'))]),
            h('div', { class: 'flex flex-wrap gap-1' }, chips),
        ]);
    }

    renderBlacklist() {
        const b = this.bl;
        if (b.error) return this.errorBox(b.error, () => this.loadBlacklists());
        if (!b.data) return this.loadingBox();
        const out = [h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, `${t('bl_title')} 💎`),
            h('p', { class: CLS.muted }, t('bl_hint'))];
        const on = b.config ? b.config.enabled !== false : true;
        if (!b.features?.double_blacklist) {
            // v45 · 📛 Lista negra básica (FREE / PRO): una lista de advertencia con tope y acción fija.
            const max = b.basicMax || 20;
            return h('div', { class: 'space-y-2' }, [
                h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, t('bl_basic_title')),
                h('p', { class: CLS.muted }, t('bl_basic_hint', { max })),
                h('button', {
                    type: 'button', class: `${CLS.chip} w-full ${on ? ON_CLS : OFF_CLS}`, disabled: b.busy, role: 'switch',
                    'aria-checked': on ? 'true' : 'false',
                    onclick: () => this.blAction({ action: 'config', config: { enabled: !on } }, () => t('saved')),
                }, `${on ? '🟢' : '🔴'} ${t('bl_on')}`),
                this.renderList('warn', (b.data.warn || []).slice(0, max), b.busy, true),
                h('p', { class: CLS.muted }, t('bl_basic_upsell')),
                this.lockBox('ultra_pro'),
            ]);
        }
        out.push(h('button', {
            type: 'button', class: `${CLS.chip} w-full ${on ? ON_CLS : OFF_CLS}`, disabled: b.busy, role: 'switch',
            'aria-checked': on ? 'true' : 'false',
            onclick: () => this.blAction({ action: 'config', config: { enabled: !on } }, () => t('saved')),
        }, `${on ? '🟢' : '🔴'} ${t('bl_on')}`));
        out.push(this.renderList('ban', b.data.ban || [], b.busy), this.renderList('warn', b.data.warn || [], b.busy),
            this.renderList('allow', b.allowlist || [], b.busy));
        return h('div', { class: 'space-y-2' }, out);
    }

    /** v44 · Acción, duración (ban / mute) y borrado del mensaje de una lista. */
    renderRule(kind, busy) {
        const rule = (this.bl.config && this.bl.config.lists && this.bl.config.lists[kind])
            || { action: kind === 'ban' ? 'ban' : 'warn', duration: 0, delete: true };
        const save = patch => this.blAction({ action: 'config', config: { lists: { [kind]: patch } } }, () => t('saved'));
        const action = select(BL_ACTIONS.map(a => [a, t(`act_${a}`)]), rule.action, value => save({ action: value }), busy);
        const durations = [...new Set([...BL_DURATIONS, Number(rule.duration) || 0])].sort((a, b) => a - b);
        const duration = (rule.action === 'ban' || rule.action === 'mute')
            ? select(durations.map(d => [d, t(`dur_${d}`) === `dur_${d}` ? `${d} min` : t(`dur_${d}`)]), Number(rule.duration) || 0,
                value => save({ duration: Number(value) }), busy)
            : null;
        const del = rule.delete !== false;
        const delBtn = h('button', {
            type: 'button', class: `${CLS.chip} px-2 ${del ? ON_CLS : OFF_CLS}`, disabled: busy, role: 'switch',
            'aria-checked': del ? 'true' : 'false', onclick: () => save({ delete: !del }),
        }, `${del ? '🗑 ✅' : '🗑 ❌'} ${t('bl_delete')}`);
        return h('div', { class: 'space-y-1' }, [
            h('p', { class: CLS.label }, t('bl_rule')),
            h('div', { class: 'flex flex-wrap items-center gap-1.5' }, [action, duration, delBtn]),
        ]);
    }

    // ---------------------------------------------------------------- Donaciones
    async loadDonations() {
        const ok = this.guard();
        this.don.error = '';
        this.render();
        const res = await serviceMessagesService.fetchDonations(this.chatId);
        if (!ok(res)) return;
        if (res.__error) this.don.error = errorText(res);
        else Object.assign(this.don, { links: res.donations?.links || [], features: res.features || {} });
        this.render();
    }

    async saveLinks(links) {
        if (!this.chatId || this.don.busy) return false;
        const ok = this.guard();
        this.don.busy = true;
        this.render();
        const res = await serviceMessagesService.saveDonations(this.chatId, links);
        if (!ok(res)) return false;
        this.don.busy = false;
        if (res.__error) {
            haptic('error');
            toast('error', '⚠️', errorText(res));
            this.render();
            return false;
        }
        haptic('ok');
        Object.assign(this.don, { links: res.donations?.links || [], label: '', url: '' });
        toast('success', '💰', t('saved'));
        this.render();
        return true;
    }

    renderDonations() {
        const d = this.don;
        if (d.error) return this.errorBox(d.error, () => this.loadDonations());
        if (!d.links) return this.loadingBox();
        const out = [h('p', { class: 'text-[11px] font-extrabold text-cyan-300' }, t('don_title')), h('p', { class: CLS.muted }, t('don_hint'))];
        if (!d.features?.donation_links) {
            out.push(this.lockBox('pro'));
            return h('div', { class: 'space-y-2' }, out);
        }
        if (!d.links.length) out.push(h('p', { class: `${CLS.muted} text-center py-1` }, t('don_empty')));
        d.links.forEach((link, i) => out.push(h('div', { class: `${CLS.card} flex items-center justify-between gap-2` }, [
            h('div', { class: 'min-w-0' }, [
                h('p', { class: 'text-[11px] font-extrabold text-theme-main truncate' }, `🔗 ${link.label}`),
                h('p', { class: 'text-[9px] font-mono text-neutral-500 truncate' }, link.url)]),
            h('button', { type: 'button', class: `${CLS.btn} border-red-400/40 bg-red-400/10 text-red-300 shrink-0`, disabled: d.busy,
                onclick: () => this.saveLinks(d.links.filter((_, j) => j !== i)) }, '🗑'),
        ])));
        if (d.links.length < LINKS_MAX) {
            const label = h('input', { type: 'text', class: CLS.input, maxlength: '32', placeholder: t('don_label'), value: d.label,
                disabled: d.busy, oninput: ev => { this.don.label = ev.target.value; } });
            const url = h('input', { type: 'url', class: CLS.input, maxlength: '512', placeholder: t('don_url'), value: d.url,
                disabled: d.busy, autocomplete: 'off', spellcheck: 'false', oninput: ev => { this.don.url = ev.target.value; } });
            out.push(h('div', { class: CLS.card }, [label, url, h('button', {
                type: 'button', class: `${CLS.btn} w-full border-cyan-400/40 bg-cyan-400/10 text-cyan-300`, disabled: d.busy,
                onclick: () => {
                    if (!label.value.trim() || !url.value.trim()) return;
                    this.saveLinks([...d.links, { label: label.value.trim(), url: url.value.trim() }]);
                },
            }, `➕ ${t('don_add')}`)]));
        }
        return h('div', { class: 'space-y-2' }, out);
    }

    // ------------------------------------------------------------------ Común
    loadingBox() {
        return h('div', { class: 'flex items-center justify-center gap-2 py-3 text-[11px] text-neutral-500 font-mono' }, [
            h('i', { class: 'fa-solid fa-circle-notch fa-spin' }), t('loading')]);
    }

    errorBox(message, retry) {
        return h('div', { class: 'rounded-xl border border-red-500/30 bg-red-500/5 p-2.5 space-y-1.5 text-center' }, [
            h('p', { class: 'text-[11px] text-red-300 break-words' }, message),
            retry ? h('button', { type: 'button', class: `${CLS.btn} border-neutral-700 bg-black/60 text-neutral-300`, onclick: retry }, t('retry')) : null,
        ]);
    }

    render() {
        const root = this.root;
        if (!root) return;
        const title = document.getElementById('svc-title');
        const desc = document.getElementById('svc-desc');
        if (title) title.textContent = t('title');
        if (desc) desc.textContent = t('desc');
        root.textContent = '';
        if (!this.chatId) {
            root.appendChild(h('p', { class: 'text-center text-[11px] text-neutral-500 font-mono py-2' }, t('pick')));
            return;
        }
        const tabs = h('div', { class: 'grid grid-cols-3 gap-1.5', role: 'tablist' },
            [['messages', 'tab_messages'], ['blacklist', 'tab_blacklist'], ['donations', 'tab_donations']].map(([id, key]) => h('button', {
                type: 'button', role: 'tab', 'aria-selected': this.tab === id ? 'true' : 'false',
                class: `${CLS.chip} ${this.tab === id ? ON_CLS : OFF_CLS}`, onclick: () => this.setTab(id),
            }, t(key))));
        const body = { messages: () => this.renderMessages(), blacklist: () => this.renderBlacklist(),
            donations: () => this.renderDonations() }[this.tab]();
        root.appendChild(tabs);
        root.appendChild(body);
    }
}

export const serviceMessagesConsole = new ServiceMessagesConsole();