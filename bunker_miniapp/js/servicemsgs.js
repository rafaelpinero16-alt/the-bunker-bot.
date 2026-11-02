/**
 * servicemsgs.js — The Bunker OS · Mini App · v43 · Fase 9
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
const KINDS = ['join', 'leave', 'videochat', 'pinned', 'chat_info'];
const NOTICE_KINDS = ['warn', 'mute', 'kick', 'ban'];
const DELAYS = CONFIG.SERVICE_MSGS?.DELAYS || [10, 30, 60, 300, 900, 3600];
const NOTICE_TTLS = CONFIG.SERVICE_MSGS?.NOTICE_TTLS || [null, 10, 30, 60, 300, 0];
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
        k_join: '👋 Joins', k_leave: '🚪 Leaves', k_videochat: '🎙️ Video chat', k_pinned: '📌 Pins', k_chat_info: '🖼️ Title / photo',
        a_keep: 'Keep', a_delete: 'Delete now', a_delay: 'Delete after…',
        notices_title: 'Bot notices', notices_hint: '«Default» uses the group\'s service TTL; «Pinned» never deletes.',
        n_warn: '⚠️ Warning', n_mute: '🔇 Mute', n_kick: '👢 Kick', n_ban: '🚫 Ban',
        ttl_default: 'Default', ttl_pinned: 'Pinned',
        custom_text: 'Custom text (HTML)', custom_hint: 'Variables: {mention} {strikes} {limit} {reason} {action}. Empty = official text.',
        appeal: '⚖️ «Appeal» button on automatic bans', appeal_hint: 'Requires ban appeals to be enabled for this community.',
        vc_title: 'Voice-chat message schedule', vc_hint: 'Outside these hours (or with posting off) the Sentinel and the bot post nothing in the chat; acoustic moderation keeps working silently.',
        vc_enabled: 'Posting', vc_all_day: 'All day', vc_start: 'From', vc_end: 'To', vc_interval: 'Minimum gap between member notices',
        vc_no_limit: 'No limit', vc_both: 'Set both times or choose «All day».',
        bl_title: 'Double Blacklist', bl_hint: 'Terms of your own (case-insensitive, whole words). A term lives in only one list.',
        bl_ban: '⛔ Ban list — bans on sight', bl_warn: '⚠️ Warn list — deletes and warns',
        bl_placeholder: 'term, another term…', bl_add: 'Add', bl_empty: 'Empty', bl_remove: 'Remove',
        bl_added: '{a} added · {m} moved · {s} already listed',
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
        k_join: '👋 Entradas', k_leave: '🚪 Salidas', k_videochat: '🎙️ Videochat', k_pinned: '📌 Fijados', k_chat_info: '🖼️ Título / foto',
        a_keep: 'Mantener', a_delete: 'Borrar ya', a_delay: 'Borrar a los…',
        notices_title: 'Avisos del bot', notices_hint: '«Por defecto» usa el TTL de servicio del grupo; «Fijo» no se borra nunca.',
        n_warn: '⚠️ Advertencia', n_mute: '🔇 Silencio', n_kick: '👢 Expulsión', n_ban: '🚫 Baneo',
        ttl_default: 'Por defecto', ttl_pinned: 'Fijo',
        custom_text: 'Texto propio (HTML)', custom_hint: 'Variables: {mention} {strikes} {limit} {reason} {action}. Vacío = texto oficial.',
        appeal: '⚖️ Botón «Apelar» en baneos automáticos', appeal_hint: 'Requiere tener activas las apelaciones de baneo en esta comunidad.',
        vc_title: 'Horario de los mensajes del videochat', vc_hint: 'Fuera de este horario (o con la emisión apagada) el Centinela y el bot no publican nada en el chat; la moderación acústica sigue en silencio.',
        vc_enabled: 'Emisión', vc_all_day: 'Todo el día', vc_start: 'Desde', vc_end: 'Hasta', vc_interval: 'Separación mínima entre avisos de miembros',
        vc_no_limit: 'Sin límite', vc_both: 'Indica las dos horas o elige «Todo el día».',
        bl_title: 'Doble Lista Negra', bl_hint: 'Términos propios (sin distinguir mayúsculas, palabra completa). Un término solo está en una lista.',
        bl_ban: '⛔ Lista de baneo — expulsa en el acto', bl_warn: '⚠️ Lista de advertencia — borra y advierte',
        bl_placeholder: 'término, otro término…', bl_add: 'Añadir', bl_empty: 'Vacía', bl_remove: 'Quitar',
        bl_added: '{a} añadidos · {m} movidos · {s} ya estaban',
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
        this.bl = { data: null, features: null, error: '', busy: false, input: { ban: '', warn: '' } };
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

    renderNotices(notices, busy, paid) {
        const items = notices.notices || {};
        const rows = NOTICE_KINDS.map(kind => {
            const item = items[kind] || {};
            const ttlSel = select(NOTICE_TTLS.map(v => [v, v === null ? t('ttl_default') : (v === 0 ? t('ttl_pinned') : fmtSecs(v))]),
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
                        t('save'))]),
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
            this.renderNotices(m.data.bot_notices || {}, m.busy, Boolean(f.bot_notice_custom)),
            this.renderVc(m.data.vc_messages || {}, m.busy, Boolean(f.vc_message_window)),
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
        else Object.assign(this.bl, { data: res.blacklists, features: res.features || {} });
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
            if (body.action === 'add') this.bl.input[body.list_type] = '';
            toast('success', '🚫', okText(res));
        }
        this.render();
    }

    renderList(kind, terms, busy) {
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
                res => t('bl_added', { a: res.added ?? 0, m: res.moved ?? 0, s: res.skipped ?? 0 }));
        };
        const chips = terms.length ? terms.map(term => h('span', {
            class: 'inline-flex items-center gap-1 rounded-full border border-neutral-700 bg-black/50 px-2 py-0.5 text-[10px] font-mono text-theme-main',
        }, [term, h('button', {
            type: 'button', class: 'text-neutral-500 hover:text-red-300 disabled:opacity-40', disabled: busy, 'aria-label': `${t('bl_remove')} ${term}`,
            onclick: () => this.blAction({ action: 'remove', terms: [term] }, () => t('saved')),
        }, '×')])) : [h('span', { class: CLS.muted }, t('bl_empty'))];
        return h('div', { class: CLS.card }, [
            h('p', { class: `text-[11px] font-extrabold ${kind === 'ban' ? 'text-red-300' : 'text-amber-300'}` },
                `${t(kind === 'ban' ? 'bl_ban' : 'bl_warn')} (${terms.length})`),
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
        if (!b.features?.double_blacklist) {
            out.push(this.lockBox('ultra_pro'));
            return h('div', { class: 'space-y-2' }, out);
        }
        out.push(this.renderList('ban', b.data.ban || [], b.busy), this.renderList('warn', b.data.warn || [], b.busy));
        return h('div', { class: 'space-y-2' }, out);
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
