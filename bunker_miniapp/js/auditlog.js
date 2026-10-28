/**
 * auditlog.js — The Bunker OS · Mini App · v41.1 · Canal de Registro en tiempo real + Auditoría MicVIP
 *
 * Módulo ES autocontenido que pinta #audit-panel en la consola de la comunidad:
 *   • 🧾 Canal de registro: vincular (@alias o -100…), desvincular, prueba de entrega y estado de salud.
 *   • 🎚️ Vectores: interruptor por cada familia de eventos que se publica AL INSTANTE en el canal.
 *   • 🎙️ MicVIP activos: comprador, Stars, compra, aplicación automática del 100 % por el Centinela y expiración
 *     exacta con cuenta atrás (sin intervención manual: todo lo registra el bot).
 *
 * Mismas garantías que moderation.js: api.request (credencial única, epoch, { __error } sin lanzar), ids validados,
 * DOM con textContent (nada del servidor se interpreta como HTML) y respuestas obsoletas descartadas.
 * The Bunker Command OS © 2026 — Cloud Media Management
 */
import { api, session } from './api.js';
import { ui } from './ui.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';

const TIMEOUT_MS = 12000;
const COUNTDOWN_MS = 30000;

const TEXTS = {
    en: {
        title: 'Real-time Log Channel', desc: 'Every enabled event is posted instantly to your log channel: no 24-hour wait.',
        pick: 'Select a community.', loading: 'Loading…', retry: 'Retry', error_generic: 'Something went wrong.',
        err_forbidden: 'Only the community owner can manage the log channel.', err_network: 'Connection problem. Try again.',
        err_missing: 'This server does not have the real-time log API yet (update the backend).',
        channel: 'Log channel', placeholder: '@channel or -100…', save: 'Link', unlink: 'Unlink', test: 'Send test',
        unlink_confirm: 'Stop posting audit cards to the log channel?', saved: 'Saved', test_sent: 'Test card sent',
        none: 'Not linked', health_ok: '🟢 The bot can post', health_fault: '🔴 The bot can NOT post', health_unknown: '⚪ Not checked',
        vectors: 'Log vectors', vectors_hint: 'Muted vectors are not posted. Changes apply instantly.',
        mv_title: 'Active MicVIP passes', mv_desc: 'Granted automatically: purchase, Stars, 100% volume confirmation by the Sentinel and exact expiry.',
        mv_empty: 'No active MicVIP passes.', mv_active: 'Active', mv_confirmed: 'Volume confirmed', mv_stars: 'Stars (last purchases)',
        mv_purchased: 'Purchased', mv_expires: 'Expires', mv_left: 'left', mv_volume_ok: '🔊 100% applied', mv_volume_wait: '⏳ awaiting Sentinel',
        mv_purchases: 'purchases', refresh: 'Refresh',
        d: 'd', h: 'h', m: 'min',
    },
    es: {
        title: 'Canal de Registro en tiempo real', desc: 'Cada evento activo se publica al instante en tu canal de registro: sin esperar 24 horas.',
        pick: 'Selecciona una comunidad.', loading: 'Cargando…', retry: 'Reintentar', error_generic: 'Algo salió mal.',
        err_forbidden: 'Solo el Dueño de la comunidad puede gestionar el canal de registro.', err_network: 'Problema de conexión. Inténtalo de nuevo.',
        err_missing: 'Este servidor aún no tiene la API de registros en tiempo real (actualiza el backend).',
        channel: 'Canal de registro', placeholder: '@canal o -100…', save: 'Vincular', unlink: 'Desvincular', test: 'Enviar prueba',
        unlink_confirm: '¿Dejar de publicar tarjetas de auditoría en el canal de registro?', saved: 'Guardado', test_sent: 'Tarjeta de prueba enviada',
        none: 'Sin vincular', health_ok: '🟢 El bot puede publicar', health_fault: '🔴 El bot NO puede publicar', health_unknown: '⚪ Sin verificar',
        vectors: 'Vectores de registro', vectors_hint: 'Los vectores silenciados no se publican. Los cambios aplican al instante.',
        mv_title: 'Pases MicVIP activos', mv_desc: 'Concedidos automáticamente: compra, Stars, confirmación del 100 % por el Centinela y expiración exacta.',
        mv_empty: 'No hay pases MicVIP activos.', mv_active: 'Activos', mv_confirmed: 'Volumen confirmado', mv_stars: 'Stars (últimas compras)',
        mv_purchased: 'Comprado', mv_expires: 'Vence', mv_left: 'restante', mv_volume_ok: '🔊 100 % aplicado', mv_volume_wait: '⏳ esperando al Centinela',
        mv_purchases: 'compras', refresh: 'Actualizar',
        d: 'd', h: 'h', m: 'min',
    },
};

function lang() { return state.currentLang === 'es' ? 'es' : 'en'; }
function t(key) { return (TEXTS[lang()] && TEXTS[lang()][key]) || TEXTS.en[key] || key; }

const INVALID_ID = Object.freeze({ __error: 'invalid_id', __status: 400 });
function chatIdPart(value) {
    const text = String(value ?? '').trim();
    return /^-\d{5,20}$/.test(text) ? text : null;
}

/** Canal destino aceptado por el servidor: @alias público, id numérico (-100…) o '' (desvincular). */
export function normalizeLogChannel(raw) {
    const text = String(raw ?? '').trim().replace(/^(?:https?:\/\/)?(?:t\.me|telegram\.me)\//i, '@');
    if (text === '') return { ok: true, value: '' };
    if (/^@[A-Za-z][A-Za-z0-9_]{4,31}$/.test(text)) return { ok: true, value: text };
    if (/^-\d{5,20}$/.test(text)) return { ok: true, value: text };
    return { ok: false, value: text };
}

export const auditLogService = {
    async fetchAuditLog(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/audit-log?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },
    /** { log_channel_id?: string, vectors?: { clave: boolean } } — 422 "campo: motivo" */
    async saveAuditLog(chatId, patch) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.post(`/chat/${chat}/audit-log`, { audit_log: patch }, { timeoutMs: 20000 });
    },
    async sendTest(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.post(`/chat/${chat}/audit-log/test`, {}, { timeoutMs: TIMEOUT_MS });
    },
    async fetchMicvipActive(chatId) {
        const chat = chatIdPart(chatId);
        if (!chat) return INVALID_ID;
        return await api.get(`/chat/${chat}/micvip/active?_=${Date.now()}`, { timeoutMs: TIMEOUT_MS, noStore: true });
    },
};

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
    input: 'flex-1 min-w-0 bg-black/60 border border-neutral-700 rounded-lg px-2.5 py-1.5 text-[11px] font-mono text-theme-main focus:outline-none focus:border-cyan-400/60',
    label: 'block text-[9px] font-bold text-theme-muted uppercase tracking-wide',
    card: 'rounded-xl border border-neutral-800 bg-black/30 p-2.5 space-y-1',
    muted: 'text-[10px] text-neutral-500 leading-snug',
};

function errorText(res) {
    if (!res) return t('error_generic');
    if (res.__status === 403) return t('err_forbidden');
    if (res.__error === 'network' || res.__error === 'timeout') return t('err_network');
    if (api.isEndpointMissing && api.isEndpointMissing(res)) return t('err_missing');
    return String(res.__error || t('error_generic')).replace(/^[a-z_]+:\s*/, '');
}

function toast(tone, icon, title, body = '') {
    ui.showToast({ key: 'audit-log', force: true, icon, tone, ttl: 3800, title, body });
}

function haptic(kind) {
    try {
        if (kind === 'ok') tgApp.hapticNotification('success');
        else if (kind === 'error') tgApp.hapticNotification('error');
        else tgApp.hapticImpact('light');
    } catch (e) { /* fuera de Telegram */ }
}

function fmtDate(ts) {
    if (!ts) return '—';
    try {
        return new Date(Number(ts) * 1000).toLocaleString(ui.locale ? ui.locale() : undefined,
            { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch (e) { return String(ts); }
}

export function fmtRemaining(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), mm = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}${t('d')} ${hh}${t('h')}`;
    if (hh > 0) return `${hh}${t('h')} ${mm}${t('m')}`;
    return `${mm}${t('m')}`;
}

class AuditLogConsole {
    constructor() {
        this.chatId = null;
        this.seq = 0;
        this.cfg = { data: null, error: '', busy: false, input: '' };
        this.mv = { data: null, error: '', loading: false, fetchedAt: 0 };
        this.timer = null;
    }

    get root() { return document.getElementById('audit-root'); }

    guard() {
        const ctx = { chatId: this.chatId, seq: this.seq, epoch: session.epoch };
        return res => !(res && res.__stale) && ctx.chatId === this.chatId && ctx.seq === this.seq && ctx.epoch === session.epoch;
    }

    start(chatId) {
        this.stop();
        this.chatId = String(chatId);
        this.seq += 1;
        this.render();
        this.load();
        this.loadMicvip();
        this.timer = setInterval(() => this.paintMicvip(), COUNTDOWN_MS);   // cuenta atrás (sin tocar el formulario)
    }

    stop() {
        clearInterval(this.timer);
        this.timer = null;
        this.chatId = null;
        this.seq += 1;
        this.cfg = { data: null, error: '', busy: false, input: '' };
        this.mv = { data: null, error: '', loading: false, fetchedAt: 0 };
        this.render();
    }

    async load() {
        const ok = this.guard();
        this.cfg.error = '';
        const res = await auditLogService.fetchAuditLog(this.chatId);
        if (!ok(res)) return;
        if (res.__error) this.cfg.error = errorText(res);
        else {
            this.cfg.data = res.audit_log;
            this.cfg.input = res.audit_log?.log_channel_id || '';
        }
        this.render();
    }

    async loadMicvip() {
        const ok = this.guard();
        Object.assign(this.mv, { loading: true, error: '' });
        if (this._mvBox && this._mvBox.isConnected) this.paintMicvip(); else this.render();
        const res = await auditLogService.fetchMicvipActive(this.chatId);
        if (!ok(res)) return;
        this.mv.loading = false;
        if (res.__error) this.mv.error = errorText(res);
        else Object.assign(this.mv, { data: res, fetchedAt: Date.now() });
        if (this._mvBox && this._mvBox.isConnected) this.paintMicvip(); else this.render();
    }

    async save(patch, okText = t('saved')) {
        if (!this.chatId || this.cfg.busy) return false;
        const ok = this.guard();
        this.cfg.busy = true;
        this.render();
        const res = await auditLogService.saveAuditLog(this.chatId, patch);
        if (!ok(res)) return false;
        this.cfg.busy = false;
        if (res.__error) {
            haptic('error');
            toast('error', '⚠️', errorText(res));
            this.render();
            return false;
        }
        haptic('ok');
        this.cfg.data = res.audit_log;
        this.cfg.input = res.audit_log?.log_channel_id || '';
        toast('success', '🧾', okText);
        this.render();
        return true;
    }

    async linkChannel(raw) {
        const norm = normalizeLogChannel(raw);
        if (!norm.ok || !norm.value) {
            toast('warn', '⚠️', t('placeholder'));
            return;
        }
        await this.save({ log_channel_id: norm.value });
    }

    async unlink() {
        const confirmFn = window.app && typeof window.app.confirmAction === 'function'
            ? window.app.confirmAction.bind(window.app) : (m => Promise.resolve(window.confirm(m)));
        if (!(await confirmFn(t('unlink_confirm')))) return;
        await this.save({ log_channel_id: '' });
    }

    async test() {
        if (!this.chatId || this.cfg.busy) return;
        const ok = this.guard();
        this.cfg.busy = true;
        this.render();
        const res = await auditLogService.sendTest(this.chatId);
        if (!ok(res)) return;
        this.cfg.busy = false;
        if (res.__error) { haptic('error'); toast('error', '⚠️', errorText(res)); }
        else { haptic('ok'); toast('success', '🧪', t('test_sent')); }
        this.render();
    }

    toggleVector(key, enabled) {
        haptic();
        return this.save({ vectors: { [key]: enabled } });
    }

    // ------------------------------------------------------------------ render
    loadingBox() {
        return h('div', { class: 'flex items-center justify-center gap-2 py-3 text-[11px] text-neutral-500 font-mono' }, [
            h('i', { class: 'fa-solid fa-circle-notch fa-spin' }), t('loading')]);
    }

    errorBox(message, retry) {
        return h('div', { class: 'rounded-xl border border-red-500/30 bg-red-500/5 p-2.5 space-y-1.5 text-center' }, [
            h('p', { class: 'text-[11px] text-red-300 break-words' }, message),
            h('button', { type: 'button', class: `${CLS.btn} border-neutral-700 bg-black/60 text-neutral-300`, onclick: retry }, t('retry')),
        ]);
    }

    healthText(health) {
        if (!health) return t('health_unknown');
        const okFlag = health.ok ?? health.can_post ?? (health.state === 'on' ? true : (health.state === 'fault' ? false : null));
        if (okFlag === true) return t('health_ok');
        if (okFlag === false) return t('health_fault');
        return t('health_unknown');
    }

    renderChannel() {
        const c = this.cfg;
        if (c.error) return this.errorBox(c.error, () => this.load());
        if (!c.data) return this.loadingBox();
        const input = h('input', {
            type: 'text', class: CLS.input, maxlength: '64', autocomplete: 'off', spellcheck: 'false',
            placeholder: t('placeholder'), value: c.input, disabled: c.busy,
            oninput: ev => { this.cfg.input = ev.target.value; },
            onkeydown: ev => { if (ev.key === 'Enter') { ev.preventDefault(); this.linkChannel(ev.target.value); } },
        });
        const linked = Boolean(c.data.log_channel_id);
        const vectors = (c.data.vectors || []).map(v => {
            const label = lang() === 'es' ? v.label_es : v.label_en;
            return h('button', {
                type: 'button', disabled: c.busy || !linked, 'aria-pressed': v.enabled ? 'true' : 'false',
                class: `w-full text-left rounded-lg border px-2.5 py-1.5 text-[10px] font-bold transition flex items-center gap-2 ${v.enabled
                    ? 'border-cyan-400/50 bg-cyan-400/10 text-cyan-200' : 'border-neutral-800 bg-black/40 text-neutral-500'}`,
                onclick: () => this.toggleVector(v.key, !v.enabled),
            }, [h('span', { class: 'shrink-0' }, v.enabled ? '🟢' : '⚪'), h('span', { class: 'shrink-0' }, v.icon),
                h('span', { class: 'min-w-0 break-words' }, label)]);
        });
        return h('div', { class: 'space-y-2' }, [
            h('span', { class: CLS.label }, t('channel')),
            h('div', { class: 'flex gap-1.5' }, [input,
                h('button', { type: 'button', class: `${CLS.btn} border-cyan-400/40 bg-cyan-400/10 text-cyan-300 shrink-0`,
                    disabled: c.busy, onclick: () => this.linkChannel(input.value) }, c.busy ? '…' : t('save'))]),
            h('div', { class: 'flex items-center justify-between gap-2' }, [
                h('p', { class: 'text-[10px] font-mono text-neutral-400 truncate' },
                    linked ? `${c.data.log_channel_id} · ${this.healthText(c.data.health)}` : t('none')),
                h('div', { class: 'flex gap-1.5 shrink-0' }, [
                    h('button', { type: 'button', class: `${CLS.btn} border-emerald-400/40 bg-emerald-400/10 text-emerald-300`,
                        disabled: c.busy || !linked, onclick: () => this.test() }, t('test')),
                    h('button', { type: 'button', class: `${CLS.btn} border-red-400/40 bg-red-400/10 text-red-300`,
                        disabled: c.busy || !linked, onclick: () => this.unlink() }, t('unlink')),
                ]),
            ]),
            h('span', { class: `${CLS.label} pt-1` }, t('vectors')),
            h('div', { class: 'grid grid-cols-1 gap-1.5' }, vectors),
            h('p', { class: CLS.muted }, t('vectors_hint')),
        ]);
    }

    renderMicvip() {
        const m = this.mv;
        const head = h('div', { class: 'flex items-center justify-between gap-2 pt-2' }, [
            h('h5', { class: 'text-[11px] font-extrabold text-amber-300 uppercase tracking-wide' }, `🎙️ ${t('mv_title')}`),
            h('button', { type: 'button', class: `${CLS.btn} border-neutral-700 bg-black/60 text-amber-300`, disabled: m.loading,
                title: t('refresh'), onclick: () => this.loadMicvip() }, h('i', { class: 'fa-solid fa-rotate' })),
        ]);
        const out = [head, h('p', { class: CLS.muted }, t('mv_desc'))];
        if (m.error) { out.push(this.errorBox(m.error, () => this.loadMicvip())); return h('div', { class: 'space-y-2' }, out); }
        if (!m.data) { out.push(this.loadingBox()); return h('div', { class: 'space-y-2' }, out); }
        const sum = m.data.summary || {};
        const stat = (label, value) => h('div', { class: 'rounded-lg border border-neutral-800 bg-black/40 p-1.5 text-center' }, [
            h('p', { class: 'text-sm font-black font-mono text-theme-main' }, String(value ?? 0)),
            h('p', { class: 'text-[8px] font-bold text-neutral-500 uppercase leading-tight' }, label)]);
        out.push(h('div', { class: 'grid grid-cols-3 gap-1' }, [
            stat(t('mv_active'), sum.active), stat(t('mv_confirmed'), sum.volume_confirmed), stat(t('mv_stars'), sum.stars_active)]));
        const passes = m.data.passes || [];
        if (!passes.length) out.push(h('p', { class: `${CLS.muted} text-center py-2` }, t('mv_empty')));
        const elapsed = Math.floor((Date.now() - (m.fetchedAt || Date.now())) / 1000);
        passes.forEach(p => {
            const remaining = p.remaining_seconds == null ? null : Math.max(0, p.remaining_seconds - elapsed);
            out.push(h('div', { class: CLS.card }, [
                h('div', { class: 'flex items-center justify-between gap-2' }, [
                    h('p', { class: 'text-[11px] font-extrabold text-theme-main truncate' }, p.full_name || `ID ${p.user_id}`),
                    h('span', { class: `text-[10px] font-black font-mono ${remaining !== null && remaining < 3600 ? 'text-red-300' : 'text-amber-300'}` },
                        remaining === null ? '—' : `${fmtRemaining(remaining)} ${t('mv_left')}`),
                ]),
                h('p', { class: 'text-[9px] font-mono text-neutral-500' },
                    `${p.username ? '@' + p.username + ' · ' : ''}${p.user_id} · ${p.last_amount_stars || 0} ⭐ · ${p.purchases || 0} ${t('mv_purchases')}`),
                h('p', { class: 'text-[9px] text-neutral-400' }, `${t('mv_purchased')}: ${fmtDate(p.last_purchased_at)} · ${t('mv_expires')}: ${fmtDate(p.expires_at)}`),
                h('p', { class: `text-[9px] font-bold ${p.sentinel_applied_at ? 'text-emerald-300' : 'text-neutral-500'}` },
                    p.sentinel_applied_at ? `${t('mv_volume_ok')} · ${fmtDate(p.sentinel_applied_at)}` : t('mv_volume_wait')),
            ]));
        });
        return h('div', { class: 'space-y-2' }, out);
    }

    render() {
        const root = this.root;
        if (!root) return;
        const title = document.getElementById('audit-title');
        const desc = document.getElementById('audit-desc');
        if (title) title.textContent = t('title');
        if (desc) desc.textContent = t('desc');
        root.textContent = '';
        if (!this.chatId) {
            root.appendChild(h('p', { class: 'text-center text-[11px] text-neutral-500 font-mono py-2' }, t('pick')));
            return;
        }
        this._mvBox = h('div', {}, this.renderMicvip());
        root.appendChild(this.renderChannel());
        root.appendChild(this._mvBox);
    }

    /** Repinta solo la lista MicVIP (cuenta atrás): el campo del canal conserva el foco y lo escrito. */
    paintMicvip() {
        if (!this.chatId || !this._mvBox || !this._mvBox.isConnected) return;
        this._mvBox.textContent = '';
        this._mvBox.appendChild(this.renderMicvip());
    }
}

export const auditLogConsole = new AuditLogConsole();
