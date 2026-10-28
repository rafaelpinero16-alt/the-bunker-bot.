/**
 * moderation.js — The Bunker OS · Mini App · v40 · Fase 3 · Dashboard de Moderación
 *
 * Módulo ES autocontenido que se integra en la consola del grupo (index.html → #mod-panel):
 *   • 🤖 Auto-Mod: activación, reglas, escalado (N infracciones / ventana → baneo permanente) y medida por infracción.
 *   • 🏆 Leaderboard de administradores (AdminStats: puntos, precisión, falsos positivos).
 *   • 🗂️ Historial de usuario: búsqueda por id / @alias / nombre, resumen y línea de tiempo con «Falso positivo».
 *   • 📦 Gestión Masiva: ban / unban de hasta 200 ids en segundo plano con progreso en vivo (sondeo del trabajo).
 *
 * Seguridad y robustez (mismo criterio que api.js / ui.js):
 *   • Todas las peticiones pasan por api.request (credencial única de la sesión, 401 global, { __error } sin lanzar).
 *   • Ids validados antes de construir rutas (sin segmentos inyectados); la lista masiva se valida en el cliente
 *     y otra vez en el servidor.
 *   • El DOM se construye con createElement + textContent: ningún dato del servidor se interpreta como HTML.
 *   • Respuestas obsoletas descartadas (cambio de comunidad o de sesión mientras la petición estaba en vuelo).
 *
 * The Bunker Command OS © 2026 — Cloud Media Management
 */
import { api, session } from './api.js';
import { ui } from './ui.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';

const TIMEOUT_MS = 12000;
const JOB_POLL_MS = 1500;
const JOB_POLL_MAX_MS = 15 * 60 * 1000;
const MAX_MASS_IDS = 200;
const HISTORY_PAGE = 100;

// ==========================================================================
// 🌐 TEXTOS (inglés por defecto, como el resto de la Mini App)
// ==========================================================================
const TEXTS = {
    en: {
        title: 'Moderation Dashboard', desc: 'Auto-Mod, admin leaderboard, user history and mass actions for this community. Owner only.',
        tab_automod: 'Auto-Mod', tab_board: 'Leaderboard', tab_history: 'History', tab_mass: 'Mass actions',
        pick: 'Select a community.', loading: 'Loading…', retry: 'Retry', error_generic: 'Something went wrong.',
        err_forbidden: 'Only the community owner can use the moderation dashboard.', err_network: 'Connection problem. Try again.',
        err_missing: 'This server does not have the moderation dashboard yet (update the backend).',
        saved: 'Saved', on: 'ON', off: 'OFF',
        am_enabled: 'Auto-Mod', am_rules: 'Rules', am_threshold: 'Infractions before permanent ban', am_window: 'Within',
        am_action: 'On each infraction', am_mute: 'Mute length', act_delete: 'Delete message', act_mute: 'Delete + mute',
        am_hint: 'Auto-Mod runs once per message after admin / whitelist / VIP exemptions. Reaching the threshold bans permanently with an evidence trail (#AUTOMOD_ACTION) and, if appeals are on, offers a paid Stars appeal.',
        rule_phishing: '🎣 Phishing', rule_scam: '💰 Scams', rule_invite_spam: '📨 Invite spam', rule_mention_flood: '📣 Mention flood',
        rule_char_flood: '🔁 Character flood', rule_repeat_flood: '♻️ Repeated messages', rule_obfuscation: '🕶️ Obfuscation',
        hours: 'h', minutes: 'min',
        lb_days: 'Window', lb_empty: 'No manual sanctions recorded yet.', lb_points: 'pts', lb_accuracy: 'accuracy',
        lb_fp: 'false positives', lb_actions: 'actions', lb_scoring: 'Ban +{ban} · Kick +{kick} · Mute +{mute} · False positive −(points + {fp})',
        hs_placeholder: 'User ID, @username or name', hs_search: 'Search', hs_none: 'No members found in the activity registry. You can also type a numeric ID.',
        hs_back: 'Back', hs_empty: 'No moderation history for this user.', hs_strikes: 'Strikes', hs_infractions: 'Auto-Mod (window)',
        hs_active: 'Active sanctions', hs_reverted: 'Reverted', hs_appeals: 'Appeals paid', hs_msgs: 'messages', hs_level: 'Lv',
        hs_global: 'Global ban flag', hs_fp: 'False positive', hs_fp_confirm: 'Revert this sanction as a false positive? The admin who applied it loses points.',
        hs_fp_done: 'Sanction reverted', hs_reverted_tag: 'reverted', hs_cleared_tag: 'cleared', hs_by: 'by',
        kind_sanction: 'Sanction', kind_infraction: 'Auto-Mod', kind_admin_action: 'Admin (Telegram)', kind_security: 'Security',
        kind_appeal: 'Appeal', kind_strikes: 'Warn ladder',
        ms_ids: 'User IDs (one per line, or separated by commas / spaces)', ms_count: '{n} valid · {bad} invalid · max {max}',
        ms_action: 'Action', ms_ban: '⛔ Ban', ms_unban: '✅ Unban', ms_duration: 'Duration', ms_perm: 'Permanent',
        ms_reason: 'Reason (shown in the log channel)', ms_run: 'Run in background', ms_confirm: '{action} {n} users? Admins and protected accounts are skipped automatically.',
        ms_running: 'Running…', ms_done: 'Finished', ms_failed: 'Failed', ms_interrupted: 'Interrupted', ms_queued: 'Queued',
        ms_summary: '{ok} applied · {skipped} skipped · {failed} failed', ms_recent: 'Recent jobs', ms_none: 'No mass actions yet.',
        ms_invalid: 'Fix the invalid IDs first.', ms_empty: 'Add at least one user ID.', ms_too_many: 'Maximum {max} users per job.',
        ms_started: 'Mass action started', ms_busy: 'Another mass action is already running in this community.',
        d1: '1 day', d7: '7 days', d30: '30 days', d90: '90 days', h1: '1 hour', h24: '24 hours',
    },
    es: {
        title: 'Dashboard de Moderación', desc: 'Auto-Mod, leaderboard de administradores, historial de usuarios y acciones masivas de esta comunidad. Solo el Dueño.',
        tab_automod: 'Auto-Mod', tab_board: 'Leaderboard', tab_history: 'Historial', tab_mass: 'Masivas',
        pick: 'Selecciona una comunidad.', loading: 'Cargando…', retry: 'Reintentar', error_generic: 'Algo salió mal.',
        err_forbidden: 'Solo el Dueño de la comunidad puede usar el dashboard de moderación.', err_network: 'Problema de conexión. Inténtalo de nuevo.',
        err_missing: 'Este servidor aún no tiene el dashboard de moderación (actualiza el backend).',
        saved: 'Guardado', on: 'ON', off: 'OFF',
        am_enabled: 'Auto-Mod', am_rules: 'Reglas', am_threshold: 'Infracciones antes del baneo permanente', am_window: 'Dentro de',
        am_action: 'Por cada infracción', am_mute: 'Duración del silencio', act_delete: 'Borrar mensaje', act_mute: 'Borrar + silenciar',
        am_hint: 'Auto-Mod actúa una vez por mensaje, después de las exenciones de admins / whitelist / VIP. Al llegar al umbral banea de forma permanente con rastro de evidencias (#AUTOMOD_ACTION) y, si las apelaciones están activas, ofrece la apelación pagada con Stars.',
        rule_phishing: '🎣 Phishing', rule_scam: '💰 Estafas', rule_invite_spam: '📨 Spam de invitaciones', rule_mention_flood: '📣 Menciones masivas',
        rule_char_flood: '🔁 Caracteres repetidos', rule_repeat_flood: '♻️ Mensajes repetidos', rule_obfuscation: '🕶️ Ofuscación',
        hours: 'h', minutes: 'min',
        lb_days: 'Ventana', lb_empty: 'Aún no hay sanciones manuales registradas.', lb_points: 'pts', lb_accuracy: 'precisión',
        lb_fp: 'falsos positivos', lb_actions: 'acciones', lb_scoring: 'Ban +{ban} · Kick +{kick} · Mute +{mute} · Falso positivo −(puntos + {fp})',
        hs_placeholder: 'ID, @usuario o nombre', hs_search: 'Buscar', hs_none: 'No hay miembros en el registro de actividad. También puedes escribir un ID numérico.',
        hs_back: 'Volver', hs_empty: 'Este usuario no tiene historial de moderación.', hs_strikes: 'Strikes', hs_infractions: 'Auto-Mod (ventana)',
        hs_active: 'Sanciones vigentes', hs_reverted: 'Revertidas', hs_appeals: 'Apelaciones pagadas', hs_msgs: 'mensajes', hs_level: 'Nv',
        hs_global: 'Marca de baneo global', hs_fp: 'Falso positivo', hs_fp_confirm: '¿Revertir esta sanción como falso positivo? El administrador que la aplicó pierde puntos.',
        hs_fp_done: 'Sanción revertida', hs_reverted_tag: 'revertida', hs_cleared_tag: 'limpiada', hs_by: 'por',
        kind_sanction: 'Sanción', kind_infraction: 'Auto-Mod', kind_admin_action: 'Admin (Telegram)', kind_security: 'Seguridad',
        kind_appeal: 'Apelación', kind_strikes: 'Escalera de advertencias',
        ms_ids: 'IDs de usuario (uno por línea, o separados por comas / espacios)', ms_count: '{n} válidos · {bad} inválidos · máx. {max}',
        ms_action: 'Acción', ms_ban: '⛔ Banear', ms_unban: '✅ Desbanear', ms_duration: 'Duración', ms_perm: 'Permanente',
        ms_reason: 'Motivo (se muestra en el canal de registro)', ms_run: 'Ejecutar en segundo plano', ms_confirm: '¿{action} a {n} usuarios? Los administradores y cuentas protegidas se omiten automáticamente.',
        ms_running: 'En curso…', ms_done: 'Terminado', ms_failed: 'Fallido', ms_interrupted: 'Interrumpido', ms_queued: 'En cola',
        ms_summary: '{ok} aplicadas · {skipped} omitidas · {failed} fallidas', ms_recent: 'Trabajos recientes', ms_none: 'Aún no hay acciones masivas.',
        ms_invalid: 'Corrige primero los IDs inválidos.', ms_empty: 'Añade al menos un ID de usuario.', ms_too_many: 'Máximo {max} usuarios por trabajo.',
        ms_started: 'Acción masiva iniciada', ms_busy: 'Ya hay una acción masiva en curso en esta comunidad.',
        d1: '1 día', d7: '7 días', d30: '30 días', d90: '90 días', h1: '1 hora', h24: '24 horas',
    },
};

function lang() { return state.currentLang === 'es' ? 'es' : 'en'; }
function t(key, vars = {}) {
    const text = (TEXTS[lang()] && TEXTS[lang()][key]) || TEXTS.en[key] || key;
    return text.replace(/\{(\w+)\}/g, (_, name) => (vars[name] !== undefined ? String(vars[name]) : `{${name}}`));
}

// ==========================================================================
// 🔌 SERVICIO DE API (moderationService)
// ==========================================================================
const INVALID_ID = Object.freeze({ __error: 'invalid_id', __status: 400 });

/** Id de chat (negativo) o de usuario / sanción (positivo) seguro para una ruta. */
function chatIdPart(value) {
    const text = String(value ?? '').trim();
    return /^-\d{5,20}$/.test(text) ? text : null;
}
function positiveIdPart(value) {
    const text = String(value ?? '').trim();
    return /^\d{1,20}$/.test(text) && !/^0+$/.test(text) ? text : null;
}
function jobIdPart(value) {
    const text = String(value ?? '').trim();
    return /^[A-Za-z0-9_-]{1,32}$/.test(text) ? text : null;
}

/**
 * Lista masiva desde texto libre: ids positivos, sin duplicados, orden conservado.
 * @returns {{ ids: string[], invalid: string[] }}
 */
export function parseUserIds(raw) {
    const ids = [];
    const invalid = [];
    const seen = new Set();
    String(raw || '').split(/[\s,;]+/).map(s => s.trim()).filter(Boolean).forEach(token => {
        const clean = token.replace(/^id[:=]?/i, '');
        if (!positiveIdPart(clean)) { invalid.push(token.slice(0, 24)); return; }
        const norm = String(BigInt(clean));
        if (!seen.has(norm)) { seen.add(norm); ids.push(norm); }
    });
    return { ids, invalid };
}

export const moderationService = {
    /** GET /api/chat/{id}/users/search?q= → { results: [{ user_id, full_name, username, message_count, last_active }] } */
    async searchUsers(chatId, query) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        const q = encodeURIComponent(String(query || '').trim().slice(0, 64));
        return await api.get(`/chat/${chat}/users/search?q=${q}&_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },

    /** GET /api/chat/{id}/users/{uid}/history → { profile, summary, items[], member_status } */
    async fetchUserHistory(chatId, userId, { limit = HISTORY_PAGE, windowHours = 24 } = {}) {
        const chat = chatIdPart(chatId);
        const user = positiveIdPart(userId);
        if (!chat || !user) return INVALID_ID;
        const q = `limit=${Number(limit) || HISTORY_PAGE}&window_hours=${Number(windowHours) || 24}&_=${Date.now()}`;
        return await api.get(`/chat/${chat}/users/${user}/history?${q}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },

    /** GET /api/chat/{id}/admin-leaderboard?days= → { leaderboard[], days, scoring } */
    async fetchLeaderboard(chatId, days = 30) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/admin-leaderboard?days=${Number(days) || 30}&_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },

    /** POST /api/chat/{id}/mass-action → 202 { job } · 409 si ya hay uno en curso · 422 validación */
    async startMassAction(chatId, { action, userIds, reason = '', durationMinutes = 0 }) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        const body = { action, user_ids: (userIds || []).slice(0, MAX_MASS_IDS), reason: String(reason || '').slice(0, 300) };
        if (action === 'ban' && Number(durationMinutes) > 0) body.duration_minutes = Number(durationMinutes);
        return await api.post(`/chat/${chat}/mass-action`, body, { timeoutMs: TIMEOUT_MS });
    },

    /** GET /api/chat/{id}/mass-action/{job_id} → { job } */
    async fetchMassJob(chatId, jobId) {
        const chat = chatIdPart(chatId);
        const job = jobIdPart(jobId);
        if (!chat || !job) return INVALID_ID;
        return await api.get(`/chat/${chat}/mass-action/${job}?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },

    /** GET /api/chat/{id}/mass-actions → { jobs[] } */
    async fetchMassJobs(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/mass-actions?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },

    /** GET /api/chat/{id}/automod → { automod, options } */
    async fetchAutomod(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/automod?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },

    /** POST /api/chat/{id}/automod — { automod: { enabled?, rules?, escalation_threshold?, … } } (todo o nada) */
    async saveAutomod(chatId, patch) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.post(`/chat/${chat}/automod`, { automod: patch }, { timeoutMs: TIMEOUT_MS });
    },

    /** POST /api/chat/{id}/sanctions/{sid}/false-positive → { sanction, admin_stats } · 409 ya revertida */
    async reportFalsePositive(chatId, sanctionId) {
        const chat = chatIdPart(chatId);
        const sid = positiveIdPart(sanctionId);
        if (!chat || !sid) return INVALID_ID;
        return await api.post(`/chat/${chat}/sanctions/${sid}/false-positive`, {}, { timeoutMs: TIMEOUT_MS });
    },
};

// ==========================================================================
// 🧱 DOM SEGURO
// ==========================================================================
/** h('div', { class: '…', onclick: fn, disabled: true }, [hijos | 'texto']) — textContent siempre. */
function h(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
        if (value === undefined || value === null || value === false) return;
        if (key === 'class') el.className = value;
        else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
        else if (key === 'dataset') Object.entries(value).forEach(([k, v]) => { el.dataset[k] = String(v); });
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
    label: 'block text-[9px] font-bold text-theme-muted uppercase tracking-wide',
    card: 'rounded-xl border border-neutral-800 bg-black/30 p-2.5 space-y-1.5',
    muted: 'text-[10px] text-neutral-500 leading-snug',
};
const ON_CLS = 'border-cyan-400/60 bg-cyan-400/10 text-cyan-300';
const OFF_CLS = 'border-neutral-800 bg-black/40 text-neutral-400';

function fmtDate(ts) {
    if (!ts) return '—';
    try {
        return new Date(Number(ts) * 1000).toLocaleString(ui.locale ? ui.locale() : undefined,
            { year: '2-digit', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch (e) { return String(ts); }
}

function errorText(res) {
    if (!res) return t('error_generic');
    if (res.__status === 403) return t('err_forbidden');
    if (res.__error === 'network' || res.__error === 'timeout') return t('err_network');
    if (api.isEndpointMissing && api.isEndpointMissing(res)) return t('err_missing');
    if (res.__status === 409 && /mass_action/.test(String(res.__error))) return t('ms_busy');
    return String(res.__error || t('error_generic'));
}

function toast(tone, icon, title, body = '') {
    ui.showToast({ key: 'mod-dashboard', force: true, icon, tone, ttl: 3800, title, body });
}

async function confirmNative(message) {
    if (window.app && typeof window.app.confirmAction === 'function') return await window.app.confirmAction(message);
    return window.confirm(message);
}

function haptic(kind) {
    try {
        if (kind === 'ok') tgApp.hapticNotification('success');
        else if (kind === 'error') tgApp.hapticNotification('error');
        else tgApp.hapticImpact('light');
    } catch (e) { /* fuera de Telegram */ }
}

// ==========================================================================
// 🧭 CONSOLA DE MODERACIÓN
// ==========================================================================
class ModerationConsole {
    constructor() {
        this.chatId = null;
        this.seq = 0;
        this.tab = 'automod';
        this.automod = { data: null, options: null, busy: false, error: '' };
        this.board = { data: null, days: 30, loading: false, error: '' };
        this.history = { query: '', results: null, searching: false, userId: null, data: null, loading: false, error: '', busyFp: null };
        this.mass = { text: '', action: 'ban', duration: 0, reason: '', submitting: false, job: null, jobs: null, error: '' };
        this.pollTimer = null;
        this.pollStarted = 0;
    }

    get root() { return document.getElementById('mod-root'); }

    /** Contexto de respuesta vigente: misma comunidad, misma sesión y misma generación. */
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
        clearTimeout(this.pollTimer);
        this.pollTimer = null;
        this.chatId = null;
        this.seq += 1;
        this.automod = { data: null, options: null, busy: false, error: '' };
        this.board = { data: null, days: this.board.days, loading: false, error: '' };
        this.history = { query: '', results: null, searching: false, userId: null, data: null, loading: false, error: '', busyFp: null };
        this.mass = { text: '', action: 'ban', duration: 0, reason: '', submitting: false, job: null, jobs: null, error: '' };
        this.render();
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
        if (this.tab === 'automod' && !this.automod.data) this.loadAutomod();
        if (this.tab === 'board' && !this.board.data) this.loadBoard();
        if (this.tab === 'mass' && !this.mass.jobs) this.loadJobs();
    }

    // ---------------------------------------------------------------- Auto-Mod
    async loadAutomod() {
        const ok = this.guard();
        this.automod.error = '';
        this.render();
        const res = await moderationService.fetchAutomod(this.chatId);
        if (!ok(res)) return;
        if (res.__error) this.automod.error = errorText(res);
        else Object.assign(this.automod, { data: res.automod, options: res.options });
        this.render();
    }

    async saveAutomod(patch) {
        if (!this.chatId || this.automod.busy || !this.automod.data) return;
        const ok = this.guard();
        this.automod.busy = true;
        this.render();
        const res = await moderationService.saveAutomod(this.chatId, patch);
        if (!ok(res)) return;
        this.automod.busy = false;
        if (res.__error) {
            haptic('error');
            toast('error', '⚠️', errorText(res));
        } else {
            haptic('ok');
            this.automod.data = res.automod;
            toast('success', '🤖', t('saved'));
        }
        this.render();
    }

    renderAutomod() {
        const a = this.automod;
        if (a.error) return this.errorBox(a.error, () => this.loadAutomod());
        if (!a.data) return this.loadingBox();
        const cfg = a.data;
        const busy = a.busy;
        const toggle = h('button', {
            type: 'button', class: `${CLS.btn} ${cfg.enabled ? 'border-emerald-400/50 bg-emerald-400/10 text-emerald-300' : 'border-neutral-700 bg-black/60 text-neutral-400'}`,
            disabled: busy, 'aria-pressed': cfg.enabled ? 'true' : 'false',
            onclick: () => this.saveAutomod({ enabled: !cfg.enabled }),
        }, `${t('am_enabled')}: ${cfg.enabled ? t('on') : t('off')}`);

        const rules = (a.options && a.options.rules) || Object.keys(cfg.rules || {});
        const ruleGrid = h('div', { class: 'grid grid-cols-2 gap-1.5' }, rules.map(rule => {
            const on = Boolean(cfg.rules && cfg.rules[rule]);
            return h('button', {
                type: 'button', class: `${CLS.chip} ${on ? ON_CLS : OFF_CLS}`, disabled: busy, 'aria-pressed': on ? 'true' : 'false',
                onclick: () => this.saveAutomod({ rules: { [rule]: !on } }),
            }, t(`rule_${rule}`));
        }));

        const limits = (a.options && a.options.limits) || {};
        const select = (field, values, current, label, fmt) => h('label', { class: 'space-y-1' }, [
            h('span', { class: CLS.label }, label),
            h('select', {
                class: CLS.input, disabled: busy,
                onchange: ev => this.saveAutomod({ [field]: field === 'infraction_action' ? ev.target.value : Number(ev.target.value) }),
            }, values.map(v => {
                const opt = h('option', { value: v }, fmt(v));
                if (String(v) === String(current)) opt.selected = true;
                return opt;
            })),
        ]);
        const [tLo, tHi] = limits.escalation_threshold || [2, 10];
        const thresholds = [];
        for (let i = tLo; i <= tHi; i += 1) thresholds.push(i);
        const windows = [1, 6, 12, 24, 48, 72, 168].filter(v => v >= ((limits.escalation_window_hours || [1])[0]) && v <= ((limits.escalation_window_hours || [1, 168])[1]));
        if (!windows.includes(cfg.escalation_window_hours)) windows.push(cfg.escalation_window_hours);
        const mutes = [5, 15, 30, 60, 180, 720, 1440];
        if (!mutes.includes(cfg.mute_minutes)) mutes.push(cfg.mute_minutes);

        return h('div', { class: 'space-y-2' }, [
            h('div', { class: 'flex items-center justify-between gap-2' }, [toggle,
                h('button', { type: 'button', class: `${CLS.btn} border-neutral-700 bg-black/60 text-cyan-300`, disabled: busy, onclick: () => { this.automod.data = null; this.loadAutomod(); } },
                    h('i', { class: 'fa-solid fa-rotate' }))]),
            h('span', { class: CLS.label }, t('am_rules')),
            ruleGrid,
            h('div', { class: 'grid grid-cols-2 gap-1.5' }, [
                select('escalation_threshold', thresholds, cfg.escalation_threshold, t('am_threshold'), v => `${v}`),
                select('escalation_window_hours', windows.sort((x, y) => x - y), cfg.escalation_window_hours, t('am_window'), v => `${v} ${t('hours')}`),
                select('infraction_action', (a.options && a.options.infraction_actions) || ['delete', 'mute'], cfg.infraction_action, t('am_action'), v => t(`act_${v}`)),
                select('mute_minutes', mutes.sort((x, y) => x - y), cfg.mute_minutes, t('am_mute'), v => `${v} ${t('minutes')}`),
            ]),
            h('p', { class: CLS.muted }, t('am_hint')),
        ]);
    }

    // ------------------------------------------------------------- Leaderboard
    async loadBoard() {
        const ok = this.guard();
        Object.assign(this.board, { loading: true, error: '' });
        this.render();
        const res = await moderationService.fetchLeaderboard(this.chatId, this.board.days);
        if (!ok(res)) return;
        this.board.loading = false;
        if (res.__error) this.board.error = errorText(res);
        else this.board.data = res;
        this.render();
    }

    setBoardDays(days) {
        if (this.board.days === days) return;
        haptic();
        this.board.days = days;
        this.board.data = null;
        this.loadBoard();
    }

    renderBoard() {
        const b = this.board;
        const days = h('div', { class: 'grid grid-cols-3 gap-1.5', role: 'radiogroup' }, [7, 30, 90].map(d => h('button', {
            type: 'button', class: `${CLS.chip} ${b.days === d ? ON_CLS : OFF_CLS}`, 'aria-checked': b.days === d ? 'true' : 'false',
            onclick: () => this.setBoardDays(d),
        }, t(`d${d}`))));
        if (b.error) return h('div', { class: 'space-y-2' }, [days, this.errorBox(b.error, () => this.loadBoard())]);
        if (!b.data || b.loading) return h('div', { class: 'space-y-2' }, [days, this.loadingBox()]);
        const rows = b.data.leaderboard || [];
        const scoring = b.data.scoring || {};
        const medal = rank => ({ 1: '🥇', 2: '🥈', 3: '🥉' }[rank] || `#${rank}`);
        const list = rows.length ? rows.map(row => h('div', { class: `${CLS.card} flex items-center gap-2.5` }, [
            h('span', { class: 'text-base w-8 text-center shrink-0' }, medal(row.rank)),
            h('div', { class: 'min-w-0 flex-1' }, [
                h('p', { class: 'text-[11px] font-extrabold text-theme-main truncate' }, row.admin_name || `ID ${row.admin_id}`),
                h('p', { class: 'text-[9px] text-neutral-500 font-mono' },
                    `${row.actions} ${t('lb_actions')} · ⛔${row.bans} 👢${row.kicks} 🔇${row.mutes} · ${row.false_positives} ${t('lb_fp')} · ${Math.round((row.accuracy || 0) * 100)}% ${t('lb_accuracy')}`),
            ]),
            h('span', { class: `text-xs font-black font-mono ${row.points < 0 ? 'text-red-400' : 'text-amber-300'}` }, `${row.points} ${t('lb_points')}`),
        ])) : [h('p', { class: `${CLS.muted} text-center py-2` }, t('lb_empty'))];
        return h('div', { class: 'space-y-2' }, [days, ...list,
            h('p', { class: CLS.muted }, t('lb_scoring', { ban: scoring.ban ?? 3, kick: scoring.kick ?? 2, mute: scoring.mute ?? 1, fp: scoring.false_positive_penalty ?? 5 }))]);
    }

    // ---------------------------------------------------------------- Historial
    async search(query) {
        const q = String(query || '').trim();
        this.history.query = q;
        if (!q || !this.chatId) return;
        if (/^\d{1,20}$/.test(q)) { this.openHistory(q); return; }
        const ok = this.guard();
        Object.assign(this.history, { searching: true, error: '', results: null });
        this.render();
        const res = await moderationService.searchUsers(this.chatId, q);
        if (!ok(res)) return;
        this.history.searching = false;
        if (res.__error) this.history.error = errorText(res);
        else this.history.results = res.results || [];
        this.render();
    }

    async openHistory(userId) {
        if (!this.chatId) return;
        haptic();
        const ok = this.guard();
        Object.assign(this.history, { userId: String(userId), data: null, loading: true, error: '' });
        this.render();
        const res = await moderationService.fetchUserHistory(this.chatId, userId);
        if (!ok(res) || this.history.userId !== String(userId)) return;
        this.history.loading = false;
        if (res.__error) this.history.error = errorText(res);
        else this.history.data = res;
        this.render();
    }

    async falsePositive(sanctionId) {
        if (!this.chatId || this.history.busyFp) return;
        if (!(await confirmNative(t('hs_fp_confirm')))) return;
        const ok = this.guard();
        this.history.busyFp = String(sanctionId);
        this.render();
        const res = await moderationService.reportFalsePositive(this.chatId, sanctionId);
        if (!ok(res)) return;
        this.history.busyFp = null;
        if (res.__error) {
            haptic('error');
            toast('error', '⚠️', errorText(res));
            this.render();
            return;
        }
        haptic('ok');
        toast('success', '↩️', t('hs_fp_done'), res.admin_stats ? `${res.admin_stats.admin_name || res.admin_stats.admin_id}: ${res.admin_stats.points} ${t('lb_points')}` : '');
        this.board.data = null;   // el leaderboard cambió
        this.openHistory(this.history.userId);
    }

    renderHistory() {
        const hs = this.history;
        const input = h('input', {
            type: 'search', class: `${CLS.input} flex-1 min-w-0`, maxlength: '64', autocomplete: 'off', placeholder: t('hs_placeholder'),
            value: hs.query, onkeydown: ev => { if (ev.key === 'Enter') { ev.preventDefault(); this.search(ev.target.value); } },
        });
        const bar = h('div', { class: 'flex gap-1.5' }, [input,
            h('button', { type: 'button', class: `${CLS.btn} border-cyan-400/40 bg-cyan-400/10 text-cyan-300 shrink-0`, disabled: hs.searching,
                onclick: () => this.search(input.value) }, hs.searching ? '…' : t('hs_search'))]);

        if (hs.userId) {
            const back = h('button', { type: 'button', class: `${CLS.btn} border-neutral-700 bg-black/60 text-neutral-300`,
                onclick: () => { Object.assign(this.history, { userId: null, data: null, error: '' }); this.render(); } }, `← ${t('hs_back')}`);
            if (hs.error) return h('div', { class: 'space-y-2' }, [back, this.errorBox(hs.error, () => this.openHistory(hs.userId))]);
            if (hs.loading || !hs.data) return h('div', { class: 'space-y-2' }, [back, this.loadingBox()]);
            return h('div', { class: 'space-y-2' }, [back, this.renderProfile(hs.data)]);
        }
        const out = [bar];
        if (hs.error) out.push(this.errorBox(hs.error, () => this.search(hs.query)));
        else if (hs.searching) out.push(this.loadingBox());
        else if (hs.results) {
            if (!hs.results.length) out.push(h('p', { class: `${CLS.muted} text-center py-2` }, t('hs_none')));
            hs.results.forEach(hit => out.push(h('button', {
                type: 'button', class: `${CLS.card} w-full text-left flex items-center justify-between gap-2 active:scale-[0.99] transition`,
                onclick: () => this.openHistory(hit.user_id),
            }, [
                h('div', { class: 'min-w-0' }, [
                    h('p', { class: 'text-[11px] font-extrabold text-theme-main truncate' }, hit.full_name || `ID ${hit.user_id}`),
                    h('p', { class: 'text-[9px] font-mono text-neutral-500' }, `${hit.username ? '@' + hit.username + ' · ' : ''}${hit.user_id}`),
                ]),
                h('span', { class: 'text-[9px] font-mono text-neutral-400 shrink-0' }, `${hit.message_count} ${t('hs_msgs')}`),
            ])));
        }
        return h('div', { class: 'space-y-2' }, out);
    }

    renderProfile(data) {
        const p = data.profile || {};
        const s = data.summary || {};
        const stat = (label, value, tone = 'text-theme-main') => h('div', { class: 'rounded-lg border border-neutral-800 bg-black/40 p-1.5 text-center' }, [
            h('p', { class: `text-sm font-black font-mono ${tone}` }, String(value ?? 0)),
            h('p', { class: 'text-[8px] font-bold text-neutral-500 uppercase leading-tight' }, label),
        ]);
        const header = h('div', { class: CLS.card }, [
            h('p', { class: 'text-xs font-extrabold text-theme-main truncate' }, p.full_name || `ID ${p.user_id}`),
            h('p', { class: 'text-[9px] font-mono text-neutral-500' },
                `${p.username ? '@' + p.username + ' · ' : ''}${p.user_id} · ${p.message_count || 0} ${t('hs_msgs')} · ${t('hs_level')} ${p.level || 1}${data.member_status ? ' · ' + data.member_status : ''}`),
            p.global_banned ? h('p', { class: 'text-[9px] font-bold text-red-400' }, `⚠️ ${t('hs_global')}`) : null,
        ]);
        const summary = h('div', { class: 'grid grid-cols-5 gap-1' }, [
            stat(t('hs_strikes'), s.strikes, s.strikes ? 'text-amber-300' : 'text-theme-main'),
            stat(t('hs_infractions'), s.infractions_window, s.infractions_window ? 'text-amber-300' : 'text-theme-main'),
            stat(t('hs_active'), s.active_sanctions, s.active_sanctions ? 'text-red-400' : 'text-theme-main'),
            stat(t('hs_reverted'), s.reverted_sanctions),
            stat(t('hs_appeals'), s.appeals_paid),
        ]);
        const items = data.items || [];
        const icon = { sanction: '⚖️', infraction: '🤖', admin_action: '👮', security: '🛡️', appeal: '💸', strikes: '⚠️' };
        const timeline = items.length ? items.map(item => {
            const canFp = item.kind === 'sanction' && item.sanction_id && item.action !== 'false_positive' && !item.reverted && !item.cleared;
            const tags = [];
            if (item.reverted) tags.push(t('hs_reverted_tag'));
            if (item.cleared) tags.push(t('hs_cleared_tag'));
            return h('div', { class: `${CLS.card} ${item.reverted || item.cleared ? 'opacity-60' : ''}` }, [
                h('div', { class: 'flex items-center justify-between gap-2' }, [
                    h('p', { class: 'text-[10px] font-extrabold text-theme-main truncate' },
                        `${icon[item.kind] || '•'} ${t(`kind_${item.kind}`)} · ${String(item.action).toUpperCase()}${item.source ? ' · ' + item.source : ''}${tags.length ? ' · ' + tags.join(', ') : ''}`),
                    h('span', { class: 'text-[9px] font-mono text-neutral-500 shrink-0' }, fmtDate(item.ts)),
                ]),
                item.reason ? h('p', { class: 'text-[10px] text-neutral-300 break-words' }, item.reason) : null,
                item.detail ? h('p', { class: 'text-[9px] text-neutral-500 font-mono break-words' }, item.detail) : null,
                item.actor_id || item.actor_name ? h('p', { class: 'text-[9px] text-neutral-500' }, `${t('hs_by')} ${item.actor_name || item.actor_id}`) : null,
                canFp ? h('button', {
                    type: 'button', class: `${CLS.btn} border-amber-400/40 bg-amber-400/10 text-amber-300`,
                    disabled: this.history.busyFp === String(item.sanction_id), onclick: () => this.falsePositive(item.sanction_id),
                }, this.history.busyFp === String(item.sanction_id) ? '…' : `↩️ ${t('hs_fp')}`) : null,
            ]);
        }) : [h('p', { class: `${CLS.muted} text-center py-2` }, t('hs_empty'))];
        return h('div', { class: 'space-y-2' }, [header, summary, ...timeline]);
    }

    // ------------------------------------------------------------ Gestión masiva
    async loadJobs() {
        const ok = this.guard();
        const res = await moderationService.fetchMassJobs(this.chatId);
        if (!ok(res)) return;
        if (res.__error) this.mass.error = errorText(res);
        else {
            this.mass.jobs = res.jobs || [];
            const running = this.mass.jobs.find(j => !j.finished);
            if (running && !this.mass.job) { this.mass.job = running; this.pollJob(running.id); }
        }
        this.render();
    }

    async submitMass() {
        const m = this.mass;
        if (!this.chatId || m.submitting || (m.job && !m.job.finished)) return;
        const { ids, invalid } = parseUserIds(m.text);
        if (invalid.length) { toast('warn', '⚠️', t('ms_invalid'), invalid.slice(0, 5).join(', ')); return; }
        if (!ids.length) { toast('warn', '⚠️', t('ms_empty')); return; }
        if (ids.length > MAX_MASS_IDS) { toast('warn', '⚠️', t('ms_too_many', { max: MAX_MASS_IDS })); return; }
        const actionLabel = m.action === 'ban' ? t('ms_ban') : t('ms_unban');
        if (!(await confirmNative(t('ms_confirm', { action: actionLabel, n: ids.length })))) return;
        const ok = this.guard();
        m.submitting = true;
        this.render();
        const res = await moderationService.startMassAction(this.chatId, {
            action: m.action, userIds: ids, reason: m.reason, durationMinutes: m.action === 'ban' ? m.duration : 0,
        });
        if (!ok(res)) return;
        m.submitting = false;
        if (res.__error) {
            haptic('error');
            toast('error', '⚠️', errorText(res));
            this.render();
            return;
        }
        haptic('ok');
        toast('success', '📦', t('ms_started'), t('ms_summary', { ok: 0, skipped: 0, failed: 0 }));
        m.job = res.job;
        m.text = '';
        this.render();
        this.pollJob(res.job.id);
    }

    pollJob(jobId) {
        clearTimeout(this.pollTimer);
        this.pollStarted = Date.now();
        const ok = this.guard();
        const tick = async () => {
            const res = await moderationService.fetchMassJob(this.chatId, jobId);
            if (!ok(res)) return;
            if (!res.__error && res.job) {
                this.mass.job = res.job;
                this.render();
                if (res.job.finished) {
                    haptic(res.job.status === 'done' ? 'ok' : 'error');
                    toast(res.job.status === 'done' ? 'success' : 'warn', '📦', t(`ms_${res.job.status}`),
                        t('ms_summary', { ok: res.job.ok, skipped: res.job.skipped, failed: res.job.failed }));
                    this.mass.jobs = null;
                    this.board.data = null;
                    this.loadJobs();
                    return;
                }
            }
            if (Date.now() - this.pollStarted < JOB_POLL_MAX_MS) this.pollTimer = setTimeout(tick, JOB_POLL_MS);
        };
        this.pollTimer = setTimeout(tick, 600);
    }

    renderJob(job) {
        const pct = Math.round((job.progress || 0) * 100);
        const tone = job.status === 'done' ? 'bg-emerald-400' : (job.status === 'failed' || job.status === 'interrupted') ? 'bg-red-400' : 'bg-cyan-400';
        const failures = (job.results || []).filter(r => r.status !== 'ok').slice(0, 8);
        return h('div', { class: CLS.card }, [
            h('div', { class: 'flex items-center justify-between gap-2' }, [
                h('p', { class: 'text-[10px] font-extrabold text-theme-main' },
                    `${job.action === 'ban' ? '⛔' : '✅'} ${String(job.action).toUpperCase()} · ${job.total} · ${t(`ms_${job.status}`)}`),
                h('span', { class: 'text-[9px] font-mono text-neutral-500' }, fmtDate(job.created_at)),
            ]),
            h('div', { class: 'h-1.5 w-full rounded-full bg-neutral-800 overflow-hidden', role: 'progressbar', 'aria-valuenow': String(pct), 'aria-valuemin': '0', 'aria-valuemax': '100' },
                h('div', { class: `h-full ${tone} transition-all`, style: `width:${pct}%` })),
            h('p', { class: 'text-[9px] font-mono text-neutral-400' }, `${job.done}/${job.total} · ${t('ms_summary', { ok: job.ok, skipped: job.skipped, failed: job.failed })}`),
            job.error ? h('p', { class: 'text-[9px] text-red-400 break-words' }, job.error) : null,
            ...failures.map(r => h('p', { class: 'text-[9px] font-mono text-neutral-500' }, `${r.user_id} · ${r.status} · ${r.code || ''}`)),
        ]);
    }

    renderMass() {
        const m = this.mass;
        const parsed = parseUserIds(m.text);
        const running = Boolean(m.job && !m.job.finished);
        const textarea = h('textarea', {
            class: `${CLS.input} font-mono h-24 resize-y`, spellcheck: 'false', autocomplete: 'off', placeholder: '123456789\n987654321',
            disabled: running || m.submitting,
            oninput: ev => { this.mass.text = ev.target.value; counter.textContent = t('ms_count', { n: parseUserIds(ev.target.value).ids.length, bad: parseUserIds(ev.target.value).invalid.length, max: MAX_MASS_IDS }); },
        });
        textarea.value = m.text;
        const counter = h('p', { class: CLS.muted }, t('ms_count', { n: parsed.ids.length, bad: parsed.invalid.length, max: MAX_MASS_IDS }));
        const actionSel = h('div', { class: 'grid grid-cols-2 gap-1.5', role: 'radiogroup' }, ['ban', 'unban'].map(a => h('button', {
            type: 'button', class: `${CLS.chip} ${m.action === a ? ON_CLS : OFF_CLS}`, disabled: running, 'aria-checked': m.action === a ? 'true' : 'false',
            onclick: () => { this.mass.action = a; this.render(); },
        }, t(`ms_${a}`))));
        const durations = [[0, t('ms_perm')], [60, t('h1')], [1440, t('h24')], [10080, t('d7')], [43200, t('d30')]];
        const duration = m.action === 'ban' ? h('label', { class: 'space-y-1' }, [
            h('span', { class: CLS.label }, t('ms_duration')),
            h('select', { class: CLS.input, disabled: running, onchange: ev => { this.mass.duration = Number(ev.target.value); } },
                durations.map(([v, label]) => { const o = h('option', { value: v }, label); if (v === m.duration) o.selected = true; return o; })),
        ]) : null;
        const reason = h('input', {
            type: 'text', class: CLS.input, maxlength: '300', placeholder: t('ms_reason'), disabled: running,
            oninput: ev => { this.mass.reason = ev.target.value; },
        });
        reason.value = m.reason;
        const run = h('button', {
            type: 'button', class: `${CLS.btn} w-full ${m.action === 'ban' ? 'border-red-400/50 bg-red-400/10 text-red-300' : 'border-emerald-400/50 bg-emerald-400/10 text-emerald-300'}`,
            disabled: running || m.submitting, onclick: () => this.submitMass(),
        }, m.submitting || running ? t('ms_running') : `${m.action === 'ban' ? t('ms_ban') : t('ms_unban')} · ${t('ms_run')}`);

        const out = [h('span', { class: CLS.label }, t('ms_ids')), textarea, counter, h('span', { class: CLS.label }, t('ms_action')), actionSel];
        if (duration) out.push(duration);
        out.push(reason, run);
        if (m.job) out.push(this.renderJob(m.job));
        out.push(h('span', { class: `${CLS.label} pt-1` }, t('ms_recent')));
        if (m.error) out.push(this.errorBox(m.error, () => { this.mass.error = ''; this.loadJobs(); }));
        else if (!m.jobs) out.push(this.loadingBox());
        else if (!m.jobs.length) out.push(h('p', { class: `${CLS.muted} text-center py-1` }, t('ms_none')));
        else m.jobs.filter(j => !m.job || j.id !== m.job.id).slice(0, 5).forEach(j => out.push(this.renderJob(j)));
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
        const title = document.getElementById('mod-title');
        const desc = document.getElementById('mod-desc');
        if (title) title.textContent = t('title');
        if (desc) desc.textContent = t('desc');
        root.textContent = '';
        if (!this.chatId) {
            root.appendChild(h('p', { class: 'text-center text-[11px] text-neutral-500 font-mono py-2' }, t('pick')));
            return;
        }
        const tabs = h('div', { class: 'grid grid-cols-4 gap-1.5', role: 'tablist' }, [
            ['automod', 'tab_automod'], ['board', 'tab_board'], ['history', 'tab_history'], ['mass', 'tab_mass'],
        ].map(([id, key]) => h('button', {
            type: 'button', role: 'tab', 'aria-selected': this.tab === id ? 'true' : 'false',
            class: `${CLS.chip} ${this.tab === id ? ON_CLS : OFF_CLS}`, onclick: () => this.setTab(id),
        }, t(key))));
        const body = { automod: () => this.renderAutomod(), board: () => this.renderBoard(),
            history: () => this.renderHistory(), mass: () => this.renderMass() }[this.tab]();
        root.appendChild(tabs);
        root.appendChild(body);
    }
}

export const moderationConsole = new ModerationConsole();
