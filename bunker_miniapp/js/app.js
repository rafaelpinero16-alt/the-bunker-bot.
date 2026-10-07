/* ==========================================================================
   THE BUNKER — COMMAND OS
   app.js — Orquestador Central Modular, Enrutador de Eventos y Ciclo de Vida
   Fase 5/6/7/8: Analítica en vivo, checkout Stars, Estudio de Canales reactivo, sesión estricta
   y gestión interactiva de Planes de Membresía del canal.
   v8.2: modal nativo del canal de registro, sincronización real de chats, difusión personalizada con
   acciones rápidas (vista previa · difundir · limpiar) y consola de control de grupos operativa.
   v8.3: generador CRUD de planes (channel_plans) y consola de programación 1:1 del bot por módulos.
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

import { CONFIG, translations } from './config.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';
import { api, session, BunkerWebSocketClient } from './api.js';
import { ui } from './ui.js';

const STUDIO_ALIAS_RE = /^@[A-Za-z][A-Za-z0-9_]{4,31}$/;
const CHAT_ID_RE = /^-?\d{5,20}$/;
const CLONE_TOKEN_RE = /^\d{6,12}:[A-Za-z0-9_-]{30,}$/;
const SWITCH_LABEL_KEYS = { captcha: 'captcha_pro', autolower: 'autolower_title', shield: 'shield_title', linklock: 'linklock_title' };
const STUDIO_TME_RE = /^(?:t\.me|telegram\.me)\/\S+$/i;

function isHttpUrl(value) {
    if (/\s/.test(value)) return false;
    try {
        const url = new URL(value);
        return (url.protocol === 'https:' || url.protocol === 'http:') && url.hostname.includes('.');
    } catch (err) {
        return false;
    }
}

/**
 * Valida y normaliza la difusión del Estudio de Canales (función pura, sin DOM). Mismas reglas que
 * channel_plans_api.validate_broadcast_fields. Devuelve { valid, values, errors } con claves i18n.
 */
export function validateStudioForm(raw) {
    const cfg = CONFIG.STUDIO;
    const errors = {};
    const values = {};

    const target = String(raw?.broadcast_target ?? '').trim();
    if (target === '' || STUDIO_ALIAS_RE.test(target) || CHAT_ID_RE.test(target)) values.broadcast_target = target;
    else errors.broadcast_target = { key: 'bc_err_target' };

    const intervals = cfg.BROADCAST_INTERVALS || [6, 12, 24, 48];
    const interval = parseInt(String(raw?.broadcast_interval ?? cfg.DEFAULT_INTERVAL ?? 12).trim(), 10);
    if (intervals.includes(interval)) values.broadcast_interval = interval;
    else errors.broadcast_interval = { key: 'bc_err_interval' };

    const promo = String(raw?.promo_text ?? '').replace(/\r\n/g, '\n').trim();
    const promoMax = cfg.PROMO_MAX || 1000;
    if (promo.length > promoMax) errors.promo_text = { key: 'bc_err_promo_long', vars: { max: promoMax } };
    else if (target !== '' && promo === '' && !errors.broadcast_target) errors.promo_text = { key: 'bc_err_promo_required' };
    else values.promo_text = promo;

    return { valid: Object.keys(errors).length === 0, values, errors };
}

function wholeNumberIn(text, min, max) {
    const clean = String(text ?? '').trim();
    if (!/^\d+$/.test(clean)) return null;
    const n = parseInt(clean, 10);
    return (n >= min && n <= max) ? n : null;
}

/**
 * Valida el Generador de Planes (función pura). Mismas reglas que channel_plans_api.validate_plan_payload:
 * el destino VIP acaba en un botón inline, así que solo se aceptan http(s), t.me o @alias.
 */
export function validatePlanForm(raw) {
    const cfg = CONFIG.PLAN_FORM;
    const errors = {};
    const values = {};

    const name = String(raw?.plan_name ?? '').trim();
    if (!name || name.length > cfg.NAME_MAX) errors.plan_name = { key: 'pf_err_name', vars: { max: cfg.NAME_MAX } };
    else values.plan_name = name;

    const price = wholeNumberIn(raw?.stars_price, cfg.PRICE_MIN, cfg.PRICE_MAX);
    if (price === null) errors.stars_price = { key: 'pf_err_price', vars: { min: cfg.PRICE_MIN, max: cfg.PRICE_MAX } };
    else values.stars_price = price;

    const days = wholeNumberIn(raw?.duration_days, cfg.DAYS_MIN, cfg.DAYS_MAX);
    if (days === null) errors.duration_days = { key: 'pf_err_days', vars: { min: cfg.DAYS_MIN, max: cfg.DAYS_MAX } };
    else values.duration_days = days;

    const link = String(raw?.target_link ?? '').trim();
    if (link === '') values.target_link = '';
    else if (link.length > cfg.LINK_MAX) errors.target_link = { key: 'pf_err_link' };
    else if (STUDIO_ALIAS_RE.test(link)) values.target_link = `https://t.me/${link.slice(1)}`;
    else if (STUDIO_TME_RE.test(link)) values.target_link = `https://${link}`;
    else if (isHttpUrl(link)) values.target_link = link;
    else errors.target_link = { key: 'pf_err_link' };

    const promo = String(raw?.promo_text ?? '').replace(/\r\n/g, '\n').trim();
    if (promo.length > cfg.PROMO_MAX) errors.promo_text = { key: 'pf_err_promo', vars: { max: cfg.PROMO_MAX } };
    else values.promo_text = promo;

    return { valid: Object.keys(errors).length === 0, values, errors };
}

/**
 * Valida los campos NO interruptor de un módulo de la consola (función pura). Mismas reglas que
 * database.validate_chat_configuration; devuelve { valid, values, errors } con claves i18n.
 */
export function validateConfigSection(section, raw) {
    const c = CONFIG.CHAT_CONFIG;
    const errors = {};
    const values = {};
    const range = (field, min, max) => {
        const n = wholeNumberIn(raw?.[field], min, max);
        if (n === null) errors[field] = { key: 'cfg_err_range', vars: { min, max } };
        else values[field] = n;
    };

    if (section === 'aduana') {
        const mode = String(raw?.captcha_mode ?? '').trim();
        if (c.CAPTCHA_MODES.includes(mode)) values.captcha_mode = mode;
        else errors.captcha_mode = { key: 'cfg_err_bool' };
        range('captcha_timeout', 30, 300);
        const welcome = String(raw?.custom_welcome ?? '').replace(/\r\n/g, '\n').trim();
        const unknown = [...welcome.matchAll(/\{([^{}]*)\}/g)].map(m => m[1]).find(v => !c.WELCOME_VARIABLES.includes(v));
        if (welcome.length > c.WELCOME_MAX) errors.custom_welcome = { key: 'cfg_err_welcome_long', vars: { max: c.WELCOME_MAX } };
        else if (unknown !== undefined) errors.custom_welcome = { key: 'cfg_err_welcome_var', vars: { var: unknown } };
        else values.custom_welcome = welcome;
    } else if (section === 'acoustic') {
        range('autolower_pct', c.AUTOLOWER_PCT_MIN, c.AUTOLOWER_PCT_MAX);
        range('micvip_price', c.STARS_MIN, c.STARS_MAX);
        range('speaker_price', c.STARS_MIN, c.STARS_MAX);
    } else if (section === 'tips') {
        const parts = String(raw?.tips_presets ?? '').split(/[\s,;]+/).filter(Boolean);
        const amounts = parts.map(p => wholeNumberIn(p, c.STARS_MIN, c.STARS_MAX));
        if (!parts.length || parts.length > c.TIP_PRESETS_MAX || amounts.some(a => a === null)) {
            errors.tips_presets = { key: 'cfg_err_presets', vars: { max: c.TIP_PRESETS_MAX } };
        } else {
            values.tips_presets = [...new Set(amounts)].sort((a, b) => a - b);
        }
    } else if (section === 'perimeter') {
        range('antiflood_rate', c.ANTIFLOOD_RATE_MIN, c.ANTIFLOOD_RATE_MAX);
        range('antiflood_window', c.ANTIFLOOD_WINDOW_MIN, c.ANTIFLOOD_WINDOW_MAX);
    }
    return { valid: Object.keys(errors).length === 0, values, errors };
}

export const app = {
    // Estado interno del Estudio de Canales (guardado reactivo)
    _studio: { channelId: null, timer: null, saving: false, dirty: false, last: null },

    // v8.3: generador de planes y consola de programación por comunidad
    _planForm: { busy: false, lastCreated: null },
    _chatConfig: { chatId: null, data: null, dirty: {}, saving: {}, switchBusy: {}, seq: 0 },
    validatePlanForm,
    validateConfigSection,

    // v8.2: difusión en curso, modal del canal de registro e interruptor con petición en vuelo
    _broadcastBusy: false,
    _logChannel: null,
    _switchBusy: null,
    _consoleBusy: {},
    validateStudioForm,

    // Estado interno de los Planes de Membresía del canal seleccionado (la UI se dibuja desde aquí)
    _plans: { channelId: null, list: [], summary: null, busy: {}, confirm: null, confirmTimer: null, links: {}, previewId: null, requestSeq: 0 },

    // Exponer accesos directos reactivos
    get currentLang() { return state.currentLang; },
    set currentLang(val) { state.currentLang = val; },
    get currentTheme() { return state.currentTheme; },
    get activeContext() { return state.activeContext; },
    get selectedChatId() { return state.selectedChatId; },
    get isSyncing() { return state.isSyncing; },
    get isAuthenticated() { return state.isAuthenticated; },
    get state() { return state.data; },

    t(key) {
        return ui.t(key);
    },

    escapeHtml(str) {
        return ui.escapeHtml(str);
    },

    init() {
        tgApp.initViewport();
        session.purgeLegacy();   // el initData cacheado por versiones anteriores no se usa nunca más
        this.initTheme();
        ui.ensureLiveStyles();
        ui.updateTranslations();
        ui.setWsStatus('idle');
        this.initLiveToastPreference();
        this.initVisibilityHandling();
        this.initDraggableButton();
        this.initCharCounter();
        this.bindChannelSelectListener();
        this.bindChannelStudio();
        this.bindPlanForm();
        this.bindConfigConsole();
        ui.setConsoleState('idle');
        ui.resetPlanForm();
        this.initUrlRouting();
        this.initAuth();
    },

    initTheme() {
        const savedTheme = localStorage.getItem('bunker_theme') || 'dark';
        this.setTheme(savedTheme);
    },

    setTheme(theme) {
        ui.setTheme(theme);
    },

    /**
     * Comunidad con la que se abrió la Mini App, por orden de prioridad:
     *  1. ?chat_id=           (botón WebApp del bot en privado: WebAppInfo(url=...?chat_id=...))
     *  2. tgWebAppStartParam  (query o hash; Direct Link  t.me/<bot>/<app>?startapp=<chat_id>)
     *  3. WebApp.initDataUnsafe.start_param
     *  4. WebApp.initDataUnsafe.chat.id   (lanzada desde el menú de adjuntos de un grupo)
     * Solo se admiten ids de comunidad (negativos): un id positivo es un chat privado.
     * Devuelve { id, source } o null.
     */
    resolveLaunchChat() {
        const toCommunityId = (raw) => {
            const match = String(raw ?? '').trim().match(/^(?:chat_)?(-\d{3,})$/);
            return match ? match[1] : '';
        };
        const query = new URLSearchParams(window.location.search || '');
        const hash = new URLSearchParams(String(window.location.hash || '').replace(/^#/, ''));
        const webApp = tgApp.tg?.initDataUnsafe || {};
        const candidates = [
            ['url', query.get('chat_id')],
            ['startapp', query.get('tgWebAppStartParam')],
            ['startapp', hash.get('tgWebAppStartParam')],
            ['webapp', webApp.start_param],
            ['webapp', webApp.chat?.id]
        ];
        for (const [source, raw] of candidates) {
            const id = toCommunityId(raw);
            if (id) return { id, source };
        }
        return null;
    },

    initUrlRouting() {
        const launch = this.resolveLaunchChat();
        const channelId = new URLSearchParams(window.location.search || '').get('channel_id');

        if (launch) {
            state.selectedChatId = launch.id;
            state.activeContext = 'groups';
            state.deepLinkTab = 'analytics';   // el botón "Abrir Dashboard en Vivo" debe aterrizar en la analítica
        } else if (/^-\d+$/.test(channelId || '')) {
            // Solo se recuerda el canal: el selector aún no tiene opciones y no hay sesión confirmada.
            // bootstrapDashboard lo selecciona cuando llegan las listas del servidor.
            state.selectedChatId = channelId;
            state.activeContext = 'channels';
            state.deepLinkTab = 'channels';
        }
    },

    /** Refleja la comunidad activa en la URL (?chat_id= / ?channel_id=) para que recargar la Mini App la conserve. */
    syncUrlChatId(chatId, isChannel = false) {
        try {
            const url = new URL(window.location.href);
            url.searchParams.delete('chat_id');
            url.searchParams.delete('channel_id');
            if (chatId) url.searchParams.set(isChannel ? 'channel_id' : 'chat_id', String(chatId));
            window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
        } catch (err) { /* entornos sin History API o URL opaca */ }
    },

    /** Nombre legible de una comunidad para el indicador del radar. */
    communityTitle(chatId) {
        const id = String(chatId);
        const known = [...state.data.groups, ...state.data.channels].find(c => String(c.id) === id);
        if (known?.title) return known.title;
        const dash = state.data.currentChatDashboard;
        if (dash?.title && String(dash.chat_id) === id) return dash.title;
        return `ID ${id}`;
    },

    initDraggableButton() {
        const btn = document.getElementById('floating-lang-btn');
        if (!btn) return;

        let isDragging = false;
        let startX = 0, startY = 0;
        let btnStartX = 0, btnStartY = 0;
        let lastTouchTime = 0;

        const onStart = (e) => {
            if (e.type === 'touchstart') {
                lastTouchTime = Date.now();
            } else if (e.type === 'mousedown') {
                if (Date.now() - lastTouchTime < 600) return;
            }

            isDragging = false;
            const evt = e.touches ? e.touches[0] : e;
            startX = evt.clientX;
            startY = evt.clientY;

            const rect = btn.getBoundingClientRect();
            btnStartX = rect.left;
            btnStartY = rect.top;

            if (e.touches) {
                document.addEventListener('touchmove', onMove, { passive: false });
                document.addEventListener('touchend', onEnd);
            } else {
                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onEnd);
            }
        };

        const onMove = (e) => {
            const evt = e.touches ? e.touches[0] : e;
            const dx = evt.clientX - startX;
            const dy = evt.clientY - startY;

            if (Math.abs(dx) > 6 || Math.abs(dy) > 6) {
                isDragging = true;
            }

            if (isDragging) {
                if (e.cancelable) e.preventDefault();
                let newX = btnStartX + dx;
                let newY = btnStartY + dy;

                const maxX = window.innerWidth - btn.offsetWidth - 8;
                const maxY = window.innerHeight - btn.offsetHeight - 8;

                newX = Math.max(8, Math.min(newX, maxX));
                newY = Math.max(8, Math.min(newY, maxY));

                btn.style.left = `${newX}px`;
                btn.style.top = `${newY}px`;
                btn.style.right = 'auto';
                btn.style.bottom = 'auto';
            }
        };

        const onEnd = () => {
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onEnd);
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onEnd);

            if (!isDragging) {
                this.toggleLanguage();
            }
        };

        btn.addEventListener('touchstart', onStart, { passive: true });
        btn.addEventListener('mousedown', onStart);
    },

    closeApp() {
        tgApp.closeApp();
    },

    toggleDrawer(show) {
        const drawer = document.getElementById('cyber-drawer');
        if (!drawer) return;
        drawer.classList.toggle('hidden', !show);
        tgApp.hapticImpact('light');
    },

    switchTab(tabId) {
        state.currentTab = tabId;
        document.querySelectorAll('.tab-view').forEach(el => el.classList.add('hidden'));

        const targetView = document.getElementById(`view-${tabId}`);
        if (targetView) targetView.classList.remove('hidden');

        tgApp.hapticImpact('light');
    },

    switchContext(val) {
        state.activeContext = val;
        this.loadStats();

        if (val === 'channels') {
            this.switchTab('channels');
        } else if (val === 'groups') {
            this.switchTab('groups');
        } else {
            this.switchTab('dashboard');
        }

        tgApp.hapticSelection();
    },

    /**
     * Activa/desactiva un módulo de moderación de la comunidad seleccionada. Solo se permite con el estado
     * real ya sincronizado desde el backend (nunca sobre un valor inventado); una petición a la vez.
     */
    async toggleSecuritySwitch(key) {
        // Compatibilidad v8.2: los cuatro interruptores heredados son campos de la consola de programación.
        const field = (CONFIG.CHAT_CONFIG?.LEGACY_SWITCHES || {})[key];
        if (!field) return false;
        return await this.toggleConfigSwitch(field);
    },

    // ======================================================================
    // 🎛️ CONSOLA DE PROGRAMACIÓN 1:1 (v8.3) — GET/POST /api/chat/{id}/configuration
    // ======================================================================
    bindConfigConsole() {
        document.querySelectorAll('#cfg-console .cfg-input').forEach(el => {
            if (el.dataset.cfgBound) return;
            el.dataset.cfgBound = 'true';
            const section = el.dataset.section;
            const onEdit = () => {
                if (el.id === 'cfg-autolower_pct') ui.updateAutolowerLabel();
                if (el.id === 'cfg-custom_welcome') ui.updateWelcomeCounter();
                ui.setFieldError(el.id, '');
                this.markConfigDirty(section);
            };
            el.addEventListener('input', onEdit);
            el.addEventListener('change', onEdit);
        });
    },

    markConfigDirty(section) {
        const cc = this._chatConfig;
        if (!cc.data || !section) return;
        cc.dirty[section] = true;
        ui.setConfigSectionStatus(section, 'dirty', ui.t('cfg_status_dirty'));
    },

    sectionTitle(section) {
        return ui.t(`cfg_sec_${section}`);
    },

    /** Carga el árbol de configuración de la comunidad (descarta respuestas de otra comunidad o sesión). */
    async loadChatConfiguration(chatId, { keepDirty = false } = {}) {
        const cc = this._chatConfig;
        const seq = ++cc.seq;
        const epoch = session.epoch;
        if (!keepDirty || cc.chatId !== String(chatId)) {
            Object.assign(cc, { chatId: String(chatId), data: null, dirty: {}, saving: {}, switchBusy: {} });
            ui.setConsoleState('loading');
        }
        const res = await api.fetchChatConfiguration(chatId);
        if (res?.__stale || epoch !== session.epoch || seq !== cc.seq || cc.chatId !== String(chatId)) return;
        if (res?.__error) {
            ui.setConsoleState('idle', ui.tf('cfg_status_error', { error: this.consoleErrorMessage(res) }));
            return;
        }
        cc.data = res.configuration || null;
        ui.setConsoleState('ready');
        const skip = Object.keys(cc.dirty).filter(sec => cc.dirty[sec]);
        ui.renderChatConfiguration(cc.data, { skipSections: skip });
        this.syncLegacySwitchState();
    },

    /** Mantiene state.securitySwitches (v8.2) coherente con la configuración real. */
    syncLegacySwitchState() {
        const cc = this._chatConfig;
        if (!cc.data) return;
        const legacy = CONFIG.CHAT_CONFIG?.LEGACY_SWITCHES || {};
        state.securitySwitches = Object.fromEntries(Object.entries(legacy).map(([key, field]) => {
            const section = ui.configSectionOf(field);
            return [key, Boolean(cc.data?.[section]?.[field])];
        }));
        state.switchesChatId = cc.chatId;
    },

    /** Interruptor de un módulo: se aplica al instante (POST parcial), con reversión si falla. */
    async toggleConfigSwitch(field) {
        const chatId = this.selectedGroupId();
        if (!chatId) return this.consolePickFirst();
        const cc = this._chatConfig;
        const section = ui.configSectionOf(field);
        if (!section || !cc.data || cc.chatId !== String(chatId)) {
            tgApp.hapticNotification('warning');
            this.consoleToast('warn', '⏳', ui.t('cfg_loading'));
            return false;
        }
        if (cc.switchBusy[field]) return false;

        const previous = Boolean(cc.data[section]?.[field]);
        const next = !previous;
        const epoch = session.epoch;
        const name = ui.t(`cfg_${field}`);
        cc.switchBusy[field] = true;
        ui.setConfigSwitch(field, next, { busy: true });
        tgApp.hapticImpact('light');

        const res = await api.updateChatConfiguration(chatId, { [section]: { [field]: next } });

        delete cc.switchBusy[field];
        if (res?.__stale || epoch !== session.epoch || cc.chatId !== String(chatId)) return false;
        if (res?.__error) {
            ui.setConfigSwitch(field, previous);
            tgApp.hapticNotification('error');
            this.consoleToast('error', '⚠️', ui.tf('gc_switch_toast_error', { name, error: this.consoleErrorMessage(res) }));
            return false;
        }
        this.applyServerConfiguration(res.configuration, { onlySwitches: true });
        tgApp.hapticNotification('success');
        this.consoleToast('success', next ? '🟢' : '🔴', ui.tf(next ? 'cfg_toast_switch_on' : 'cfg_toast_switch_off', { name }));
        return true;
    },

    /**
     * Aplica el árbol devuelto por el servidor. onlySwitches=true solo repinta interruptores (los campos con
     * ediciones pendientes de otros módulos no se pisan).
     */
    applyServerConfiguration(configuration, { onlySwitches = false, sections = null } = {}) {
        const cc = this._chatConfig;
        if (!configuration) return;
        cc.data = configuration;
        if (onlySwitches) {
            Object.entries(ui.CONFIG_LAYOUT).forEach(([section, fields]) => {
                Object.entries(fields).forEach(([field, kind]) => {
                    if (kind === 'switch' && !cc.switchBusy[field]) ui.setConfigSwitch(field, Boolean(configuration[section]?.[field]));
                });
            });
        } else {
            // Con `sections` solo se repintan esos módulos; sin él, todos menos los que tienen ediciones pendientes.
            const all = Object.keys(ui.CONFIG_LAYOUT);
            const skip = sections ? all.filter(sec => !sections.includes(sec)) : all.filter(sec => cc.dirty[sec]);
            ui.renderChatConfiguration(configuration, { skipSections: skip });
        }
        this.syncLegacySwitchState();
    },

    /** 💾 Guardar módulo: valida en cliente, envía el lote del módulo y refleja lo que guardó el servidor. */
    async saveConfigSection(section) {
        const chatId = this.selectedGroupId();
        if (!chatId) return this.consolePickFirst();
        const cc = this._chatConfig;
        if (!cc.data || cc.chatId !== String(chatId) || cc.saving[section]) return false;

        const result = validateConfigSection(section, ui.readConfigSection(section));
        Object.keys(ui.CONFIG_LAYOUT[section]).forEach(field => {
            const err = result.errors[field];
            if (ui.CONFIG_LAYOUT[section][field] !== 'switch') ui.setFieldError(`cfg-${field}`, err ? ui.tf(err.key, err.vars) : '');
        });
        if (!result.valid) {
            ui.setConfigSectionStatus(section, 'invalid', ui.t('cfg_status_invalid'));
            tgApp.hapticNotification('error');
            return false;
        }

        const epoch = session.epoch;
        cc.saving[section] = true;
        ui.setConfigSectionBusy(section, true);
        ui.setConfigSectionStatus(section, 'saving', ui.t('cfg_saving'));
        tgApp.hapticImpact('medium');
        const res = await api.updateChatConfiguration(chatId, { [section]: result.values });
        delete cc.saving[section];
        ui.setConfigSectionBusy(section, false);
        if (res?.__stale || epoch !== session.epoch || cc.chatId !== String(chatId)) return false;

        if (res?.__error) {
            const match = /^([a-z_]+):\s*(.+)$/.exec(String(res.__error));
            if (match && res.__status === 422 && document.getElementById(`cfg-${match[1]}`)) {
                ui.setFieldError(`cfg-${match[1]}`, match[2]);
                ui.setConfigSectionStatus(section, 'invalid', ui.t('cfg_status_invalid'));
            } else {
                ui.setConfigSectionStatus(section, 'error', ui.tf('cfg_status_error', { error: this.consoleErrorMessage(res) }));
            }
            tgApp.hapticNotification('error');
            this.consoleToast('error', '⚠️', ui.tf('cfg_toast_error', { section: this.sectionTitle(section), error: this.consoleErrorMessage(res) }));
            return false;
        }

        cc.dirty[section] = false;
        this.applyServerConfiguration(res.configuration, { sections: [section] });
        const time = new Date().toLocaleTimeString(state.currentLang === 'es' ? 'es-CO' : 'en-US', { hour12: false });
        ui.setConfigSectionStatus(section, 'saved', ui.tf('cfg_status_saved', { time }));
        tgApp.hapticNotification('success');
        this.consoleToast('success', '✅', ui.tf('cfg_toast_saved', { section: this.sectionTitle(section) }));
        return true;
    },

    /** Inserta {name} / {username} / {title} en el cursor del mensaje de bienvenida. */
    insertWelcomeVariable(name) {
        const area = document.getElementById('cfg-custom_welcome');
        if (!area || area.disabled || !(CONFIG.CHAT_CONFIG?.WELCOME_VARIABLES || []).includes(name)) return;
        const token = `{${name}}`;
        const start = area.selectionStart ?? area.value.length;
        const end = area.selectionEnd ?? area.value.length;
        area.value = `${area.value.slice(0, start)}${token}${area.value.slice(end)}`;
        const pos = start + token.length;
        try { area.setSelectionRange(pos, pos); } catch (err) { /* algunos WebViews no lo admiten */ }
        area.focus();
        ui.updateWelcomeCounter();
        this.markConfigDirty('aduana');
        tgApp.hapticSelection();
    },

    /** Evento 'settings_updated' del radar: otro dispositivo (o el bot) cambió la configuración. */
    onRemoteSettingsUpdated(chatId, data) {
        const cc = this._chatConfig;
        if (cc.chatId !== String(chatId)) return;
        if (data?.configuration && cc.data) {
            const before = JSON.stringify(cc.data);
            this.applyServerConfiguration(data.configuration);
            if (before !== JSON.stringify(cc.data)) {
                Object.keys(ui.CONFIG_LAYOUT).forEach(sec => {
                    if (!cc.dirty[sec] && !cc.saving[sec]) ui.setConfigSectionStatus(sec, 'remote', ui.t('cfg_status_remote'));
                });
            }
        } else {
            this.loadChatConfiguration(chatId, { keepDirty: true });
        }
    },

    // ---- Utilidades de la consola de grupos (v8.2) ----
    selectedGroupId() {
        return document.getElementById('group-owner-select')?.value || state.selectedChatId || null;
    },

    consoleToast(tone, icon, title, body = '') {
        ui.showToast({ key: 'console-action', force: true, icon, tone, ttl: CONFIG.CONSOLE?.TOAST_MS || 3600, title, body });
    },

    consolePickFirst() {
        tgApp.hapticNotification('warning');
        this.consoleToast('warn', '⚠️', ui.t('gc_pick_first'));
        return false;
    },

    consoleErrorMessage(res) {
        if (res?.__status === 403) return ui.t('plans_err_forbidden');
        if (res?.__error === 'timeout' || res?.__error === 'network') return ui.t('load_error');
        return String(res?.__error || ui.t('gc_err_generic'));
    },

    /**
     * Confirmación nativa: Telegram.WebApp.showConfirm (los WebViews de Telegram pueden descartar
     * window.confirm igual que window.prompt); fuera de Telegram, window.confirm.
     */
    confirmAction(message) {
        const wa = window.Telegram?.WebApp;
        const native = wa && wa.initData && typeof wa.showConfirm === 'function'
            && (typeof wa.isVersionAtLeast !== 'function' || wa.isVersionAtLeast('6.2'));
        if (native) {
            return new Promise(resolve => {
                try {
                    wa.showConfirm(String(message).slice(0, 250), ok => resolve(Boolean(ok)));
                } catch (err) {
                    resolve(window.confirm(message));
                }
            });
        }
        return Promise.resolve(window.confirm(message));
    },

    /** Ejecuta una acción de la consola con su botón ocupado; devuelve la respuesta o null si quedó obsoleta. */
    async runConsoleAction(buttonId, chatId, request) {
        if (this._consoleBusy[buttonId]) return null;
        const epoch = session.epoch;
        this._consoleBusy[buttonId] = true;
        ui.setButtonBusy(buttonId, true);
        try {
            const res = await request(chatId);
            if (res?.__stale || epoch !== session.epoch) return null;
            return res;
        } finally {
            delete this._consoleBusy[buttonId];
            ui.setButtonBusy(buttonId, false);
        }
    },

    toggleLanguage() {
        state.currentLang = state.currentLang === 'es' ? 'en' : 'es';
        const indicator = document.getElementById('lang-indicator');
        if (indicator) indicator.innerText = state.currentLang.toUpperCase();

        tgApp.hapticImpact('light');
        ui.updateTranslations();
        ui.renderChatList('channels-list', state.data.channels, ui.t('no_channels'));
        ui.renderChatList('groups-list', state.data.groups, ui.t('no_groups'));
        ui.renderSubscriberList(state.data.subscribers);

        // Analítica en vivo: los textos generados por JS se repintan en el nuevo idioma.
        ui.setWsStatus(state.wsStatus);
        ui.renderFeed();
        ui.renderVoiceCard();
        if (state.liveAnalytics) ui.renderAnalytics(state.liveAnalytics);

        // Planes de membresía: tarjetas, estados y vista previa se repintan en el nuevo idioma.
        this.renderPlans();
        const previewPlan = this._plans.previewId !== null ? this.planById(this._plans.previewId) : null;
        if (previewPlan) ui.renderPlanPreview(previewPlan);

        // v8.2: estados dinámicos sin data-i18n (interruptores, difusión y vista previa abierta).
        const cc = this._chatConfig;
        if (!cc.chatId) ui.setConsoleState('idle');
        else if (!cc.data) ui.setConsoleState('loading');
        if (this._planForm.lastCreated) ui.renderPlanFormResult(this._planForm.lastCreated);
        ui.updatePlanPromoCounter();
        ui.setBroadcastStatus(this._studio.broadcast || null);
        if (this._lastBroadcastPreview) ui.renderBroadcastPreview(this._lastBroadcastPreview);

        if (state.selectedChatId && state.data.currentChatDashboard) {
            this.loadChatDashboard(state.selectedChatId);
        }
    },

    openHelpModal() { document.getElementById('modal-help')?.classList.remove('hidden'); },
    closeHelpModal() { document.getElementById('modal-help')?.classList.add('hidden'); },
    openInstructionModal() { this.closeHelpModal(); document.getElementById('modal-instruction')?.classList.remove('hidden'); },
    closeInstructionModal() { document.getElementById('modal-instruction')?.classList.add('hidden'); },
    openRegexModal() { this.closeHelpModal(); document.getElementById('modal-regex')?.classList.remove('hidden'); },
    closeRegexModal() { document.getElementById('modal-regex')?.classList.add('hidden'); },

    switchHelpTab(tab) {
        const tabErr = document.getElementById('help-tab-error');
        const tabRev = document.getElementById('help-tab-review');
        const starContainer = document.getElementById('star-rating-container');

        if (tab === 'error') {
            tabErr.className = "text-[#00f3ff] pb-1 border-b-2 border-[#00f3ff]";
            tabRev.className = "text-neutral-400 pb-1";
            starContainer?.classList.add('hidden');
        } else {
            tabRev.className = "text-[#00f3ff] pb-1 border-b-2 border-[#00f3ff]";
            tabErr.className = "text-neutral-400 pb-1";
            starContainer?.classList.remove('hidden');
        }
    },

    setRating(stars) {
        const starIcons = document.querySelectorAll('#star-rating-container i');
        starIcons.forEach((icon, idx) => {
            if (idx < stars) {
                icon.className = "fa-solid fa-star cursor-pointer text-amber-400";
            } else {
                icon.className = "fa-regular fa-star cursor-pointer text-neutral-500";
            }
        });
        state.selectedRating = stars;
    },

    handleFileAttach(input) {
        const file = input.files && input.files[0];
        const label = document.getElementById('help-file-label');
        if (file && label) {
            label.innerText = `📎 ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
            label.classList.add('text-[#00f3ff]');
        }
    },

    initCharCounter() {
        const textarea = document.getElementById('help-textarea');
        const counter = document.getElementById('help-char-counter');
        if (!textarea || !counter) return;
        const max = textarea.getAttribute('maxlength') || 4096;
        textarea.addEventListener('input', () => {
            counter.innerText = `${textarea.value.length} / ${max}`;
        });
    },

    sendHelpReport() {
        const textarea = document.getElementById('help-textarea');
        const text = textarea?.value.trim() || '';
        if (!text) {
            alert(state.currentLang === 'es' ? '⚠️ Escribe un mensaje antes de enviar.' : '⚠️ Write a message before sending.');
            return;
        }
        alert(state.currentLang === 'es' ? '✅ Enviado con éxito al equipo técnico.' : '✅ Sent successfully to technical team.');
        if (textarea) textarea.value = '';
        const counter = document.getElementById('help-char-counter');
        if (counter) counter.innerText = `0 / ${textarea?.getAttribute('maxlength') || 4096}`;
        const label = document.getElementById('help-file-label');
        if (label) {
            label.innerText = ui.t('click_attach');
            label.classList.remove('text-[#00f3ff]');
        }
        this.closeHelpModal();
    },

    testRegex() {
        const patternStr = document.getElementById('regex-pattern')?.value || '';
        let testStr = document.getElementById('regex-string')?.value || '';
        const lowercase = document.getElementById('regex-lowercase')?.checked;

        if (!patternStr) {
            alert(state.currentLang === 'es' ? '⚠️ Ingresa una expresión regular.' : '⚠️ Enter a regular expression.');
            return;
        }
        if (lowercase) testStr = testStr.toLowerCase();

        try {
            const re = new RegExp(patternStr);
            const isMatch = re.test(testStr);
            alert(isMatch
                ? (state.currentLang === 'es' ? '✅ Coincidencia exitosa (Match Successful)' : '✅ Match Successful')
                : (state.currentLang === 'es' ? '❌ Sin coincidencia (No Match)' : '❌ No Match'));
        } catch (err) {
            alert(state.currentLang === 'es' ? `⚠️ Expresión inválida: ${err.message}` : `⚠️ Invalid expression: ${err.message}`);
        }
    },

    loadTelegramUser() {
        // Dentro de Telegram la identidad es SOLO la del initData; fuera, SOLO la de la sesión web.
        const tgUser = session.telegramInitData() ? tgApp.tg?.initDataUnsafe?.user : null;
        const webUser = state.webUser;
        const user = tgUser || (webUser ? {
            id: webUser.id,
            first_name: webUser.first_name || 'Operador',
            last_name: '',
            username: webUser.username || '',
            photo_url: webUser.photo_url || null
        } : null);

        ui.renderUserProfile(user);
    },

    loadAffiliateLink() {
        ui.renderAffiliateLink(session.userId());
    },

    copyAffiliateLink() {
        const userId = session.userId();
        if (!userId) return;   // sin identidad de sesión no existe enlace que copiar (nunca el de otro operador)
        this.copyText(`https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}`);
    },

    // ======================================================================
    // 🔐 SESIÓN ESTRICTA POR USUARIO
    // ======================================================================

    /** Fija la identidad de la sesión; si cambió respecto a la anterior purga todo lo cargado con ella. */
    bindSession() {
        const { changed } = session.bind();
        if (changed) this.resetSessionState();
        return changed;
    },

    /**
     * Vacía todo dato asociado a un operador (memoria, DOM, socket, temporizadores) e invalida las
     * respuestas en vuelo. No toca la sesión en sí (token, webUser) ni las preferencias (idioma, tema).
     */
    resetSessionState() {
        session.invalidate();
        this.disconnectLiveRadar();

        const studio = this._studio;
        clearTimeout(studio.timer);
        Object.assign(studio, { channelId: null, timer: null, saving: false, dirty: false, last: null });
        ui.setStudioBusy(false);
        this.resetPlansState();

        state.chatEpoch += 1;
        state.selectedChatId = null;
        state.deepLinkTab = null;
        state.activeContext = 'global';
        state.securitySwitches = null;     // desconocido hasta leer el dashboard de una comunidad
        this._chatConfig.seq += 1;          // invalida cargas de configuración en vuelo
        Object.assign(this._chatConfig, { chatId: null, data: null, dirty: {}, saving: {}, switchBusy: {} });
        this._planForm = { busy: false, lastCreated: null };
        state.switchesChatId = null;
        this._switchBusy = null;
        this._consoleBusy = {};
        this._broadcastBusy = false;
        this._logChannel = null;
        this._lastBroadcastPreview = null;
        studio.broadcast = null;
        state.data = {
            stats: { subscribers: 0, revenue_stars: 0, verified: 0, expelled: 0, purges: 0 },
            channels: [],
            groups: [],
            subscribers: [],
            currentChatDashboard: null,
            currentChatStats: null,
            currentChatAdmins: [],
            currentChatTopUsers: []
        };

        ui.clearToasts();
        ui.resetSessionView();
        const context = document.getElementById('target-context-select');
        if (context) context.value = 'global';
    },

    /** 401 en Telegram: el initData caducó (>24 h) o fue rechazado y no se puede renovar desde dentro. */
    onTelegramSessionExpired() {
        if (this._tgExpiredShown) return;
        this._tgExpiredShown = true;
        session.invalidate();
        this.disconnectLiveRadar();
        ui.showSessionExpired(true);
    },

    /** 401 en el navegador: la sesión web caducó. Se purga todo y se vuelve al login. */
    onWebSessionExpired() {
        if (!state.isAuthenticated) return;   // ya se gestionó (varias peticiones pueden fallar a la vez)
        this.resetSessionState();
        session.purgeStored();
        session.unbind();
        state.isAuthenticated = false;
        state.webUser = null;
        this.toggleDrawer(false);
        this.showLoginGate('login_expired');
    },

    logout() {
        if (!confirm(state.currentLang === 'es' ? "¿Deseas cerrar la sesión de The Bunker OS?" : "Close The Bunker OS session?")) return;

        // El modo se decide por la credencial real, NO por la existencia de window.Telegram.WebApp:
        // el SDK la define también en un navegador normal, donde close() no hace nada.
        const inTelegram = Boolean(session.telegramInitData());
        this.resetSessionState();
        session.purgeStored();
        session.unbind();

        if (inTelegram) {
            tgApp.closeApp();
            return;
        }
        state.isAuthenticated = false;
        state.webUser = null;
        this.toggleDrawer(false);
        this.showLoginGate();
    },

    initAuth() {
        if (session.telegramInitData()) {
            state.isAuthenticated = true;
            this.bindSession();
            this.showAppShell();
            this.bootstrapDashboard();
            return;
        }

        const urlParams = new URLSearchParams(window.location.search);
        const urlToken = urlParams.get('token');
        if (urlToken) {
            this.exchangeWebToken(urlToken);
            return;
        }

        const savedToken = session.webToken();
        if (savedToken) {
            this.verifyWebSession(savedToken);
        } else {
            this.showLoginGate();
        }
    },

    showLoginGate(msgKey = null) {
        this.disconnectLiveRadar();
        document.getElementById('login-gate')?.classList.remove('hidden');
        document.getElementById('app-shell')?.classList.add('hidden');
        const statusEl = document.getElementById('login-status-msg');
        if (statusEl && msgKey) statusEl.innerText = ui.t(msgKey);
        tgApp.renderTelegramWidget('app.handleTelegramWidgetAuth');
    },

    showAppShell() {
        document.getElementById('login-gate')?.classList.add('hidden');
        document.getElementById('app-shell')?.classList.remove('hidden');
    },

    async bootstrapDashboard() {
        this.loadTelegramUser();
        this.loadAffiliateLink();
        await Promise.all([
            this.loadStats(),
            this.loadChannels(),
            this.loadGroups(),
            this.loadSubscribers()
        ]);

        if (state.selectedChatId) {
            const tab = state.deepLinkTab;
            state.deepLinkTab = null;
            this.configureChat(state.selectedChatId, tab ? { tab } : {});
        }
    },

    async handleTelegramWidgetAuth(user) {
        const statusEl = document.getElementById('login-status-msg');
        if (statusEl) statusEl.innerText = ui.t('login_verifying');
        const data = await api.authenticateWidget(user);
        if (data?.status === 'success' && data.session_token) {
            localStorage.setItem('bunker_session_token', data.session_token);
            state.webUser = data.user;
            state.isAuthenticated = true;
            this.bindSession();
            this.showAppShell();
            this.bootstrapDashboard();
            tgApp.hapticNotification('success');
        } else {
            if (statusEl) statusEl.innerText = ui.t('login_error');
        }
    },

    async exchangeWebToken(tempToken) {
        const statusEl = document.getElementById('login-status-msg');
        if (statusEl) statusEl.innerText = ui.t('login_verifying');
        const data = await api.exchangeWebToken(tempToken);
        if (data?.status === 'success' && data.session_token) {
            localStorage.setItem('bunker_session_token', data.session_token);
            try {
                // Se retira solo el token temporal: ?chat_id= y demás parámetros se conservan.
                const url = new URL(window.location.href);
                url.searchParams.delete('token');
                window.history.replaceState({}, document.title, `${url.pathname}${url.search}${url.hash}`);
            } catch (err) {
                window.history.replaceState({}, document.title, window.location.pathname);
            }
            state.webUser = data.user;
            state.isAuthenticated = true;
            this.bindSession();
            this.showAppShell();
            this.bootstrapDashboard();
        } else {
            if (statusEl) statusEl.innerText = ui.t('login_expired');
            this.showLoginGate();
        }
    },

    async verifyWebSession(token) {
        const data = await api.verifyWebSession(token);
        if (data?.status === 'success') {
            state.webUser = { id: data.user_id, first_name: data.first_name, username: data.username };
            state.isAuthenticated = true;
            this.bindSession();
            this.showAppShell();
            this.bootstrapDashboard();
        } else {
            session.purgeStored();
            this.showLoginGate();
        }
    },

    loginViaBot() {
        const url = `https://t.me/${CONFIG.BOT_USERNAME}?start=weblogin`;
        window.open(url, '_blank', 'noopener,noreferrer');
        const statusEl = document.getElementById('login-status-msg');
        if (statusEl) {
            statusEl.innerText = state.currentLang === 'es'
                ? '📲 Continúa el proceso en Telegram y vuelve a esta pestaña.'
                : '📲 Continue in Telegram, then return to this tab.';
        }
    },

    async loadStats() {
        const data = await api.fetchStats(state.activeContext);
        if (!data?.__error) {
            ui.renderStats(data);
        }
    },

    /**
     * POST /api/sync-chats: el servidor audita en Telegram los chats registrados, vincula los que administra
     * el operador y devuelve los listados ya limpios; los selectores se pueblan sin otra petición.
     */
    async syncChats() {
        if (state.isSyncing) return;
        state.isSyncing = true;
        const epoch = session.epoch;

        const syncButtons = document.querySelectorAll('.sync-btn-trigger');
        syncButtons.forEach(btn => {
            if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>${ui.escapeHtml(ui.t('syncing'))}</span>`;
            btn.disabled = true;
        });
        const restoreButtons = () => syncButtons.forEach(btn => {
            if (btn.dataset.originalHtml) btn.innerHTML = btn.dataset.originalHtml;
            btn.disabled = false;
        });

        tgApp.hapticImpact('medium');

        try {
            const res = await api.syncChats();
            if (res?.__stale || epoch !== session.epoch) return;

            if (res?.__error) {
                tgApp.hapticNotification('error');
                ui.showToast({ key: 'sync', force: true, icon: '⚠️', tone: 'error', ttl: 4500, title: ui.t('sync_toast_error'), body: String(res.__error) });
                return;
            }

            const tasks = [this.loadStats(), this.loadSubscribers()];
            if (Array.isArray(res?.channels)) this.applyChannels(res.channels); else tasks.push(this.loadChannels());
            if (Array.isArray(res?.groups)) this.applyGroups(res.groups); else tasks.push(this.loadGroups());
            await Promise.all(tasks);
            if (epoch !== session.epoch) return;

            tgApp.hapticNotification('success');
            ui.showToast({
                key: 'sync', force: true, icon: '🔄', tone: 'success', ttl: 4500,
                title: ui.t('sync_toast_title'),
                body: res?.throttled
                    ? ui.t('sync_toast_throttled')
                    : ui.tf('sync_toast_body', {
                        channels: res?.total_channels ?? state.data.channels.length,
                        groups: res?.total_groups ?? state.data.groups.length,
                        linked: res?.newly_linked ?? 0
                    })
            });
        } finally {
            restoreButtons();
            state.isSyncing = false;
        }
    },

    openAddBot(type) {
        const url = `https://t.me/${CONFIG.BOT_USERNAME}?start${type === 'channel' ? 'channel' : 'group'}=admin&admin=change_info+post_messages+edit_messages+delete_messages+restrict_members+invite_users+pin_messages+promote_members+manage_video_chats`;
        tgApp.openTelegramLink(url);
    },

    studioDefaults() {
        return {
            broadcast_target: '',
            broadcast_interval: CONFIG.STUDIO.DEFAULT_INTERVAL ?? 12,
            promo_text: ''
        };
    },

    /** Configuración de difusión que devuelve el servidor (dashboard o respuesta del guardado). */
    broadcastFromServer(data) {
        if (!data || data.broadcast_interval === undefined) return null;
        return {
            broadcast_target: data.broadcast_target || '',
            broadcast_interval: Number(data.broadcast_interval) || (CONFIG.STUDIO.DEFAULT_INTERVAL ?? 12),
            promo_text: data.promo_text || '',
            broadcast_enabled: Boolean(data.broadcast_enabled)
        };
    },

    /** Rellena el formulario del Estudio con lo que guarda el servidor (sin pisar ediciones pendientes). */
    loadStudioFromServer(channelId) {
        const studio = this._studio;
        const defaults = this.studioDefaults();
        const epoch = session.epoch;
        return api.fetchChatDashboard(channelId).then(data => {
            if (!data || data.__error || data.__stale) return;
            if (epoch !== session.epoch || studio.channelId !== channelId || studio.dirty) return;
            const broadcast = this.broadcastFromServer(data);
            const values = {
                broadcast_target: broadcast ? broadcast.broadcast_target : defaults.broadcast_target,
                broadcast_interval: broadcast ? broadcast.broadcast_interval : defaults.broadcast_interval,
                promo_text: broadcast ? broadcast.promo_text : defaults.promo_text
            };
            ui.fillStudioForm(values);
            studio.last = values;
            studio.broadcast = broadcast;
            ui.setBroadcastStatus(broadcast);
        });
    },

    onChannelSelectChange(channelId) {
        const studio = this._studio;
        const idLabel = document.getElementById('channel-id-display') || document.getElementById('channel-id-text');
        if (idLabel) {
            idLabel.innerText = channelId ? `ID: ${channelId}` : 'ID: —';
        }

        let sameSelection = false;
        if (channelId === studio.channelId) {
            // Misma selección (p. ej. recarga de listas): solo se refresca si no hay ediciones pendientes.
            if (studio.dirty) return;
            sameSelection = true;
        } else {
            // Cambio de canal con ediciones pendientes: se guardan en el canal ANTERIOR antes de cargar el nuevo.
            // saveChannelStudio lee y valida el formulario de forma síncrona, antes de cualquier await.
            if (studio.channelId && studio.dirty) this.saveChannelStudio({ auto: true });

            clearTimeout(studio.timer);
            studio.timer = null;
            studio.channelId = channelId || null;
            studio.dirty = false;
            studio.last = null;
            ui.clearStudioErrors();
            ui.setStudioStatus('idle');
        }

        if (!channelId) {
            ui.fillStudioForm(this.studioDefaults());
            studio.broadcast = null;
            ui.setBroadcastStatus(null);
            this.loadChannelPlans(null);
            return;
        }

        state.selectedChatId = channelId;

        // Ajustes del canal (autorrelleno) y planes de membresía: ambos se piden de inmediato al elegir canal.
        this.loadStudioFromServer(channelId);
        this.loadChannelPlans(channelId, { silent: sameSelection });
    },

    bindChannelSelectListener() {
        const select = document.getElementById('channel-owner-select');
        if (!select || select.dataset.bound) return;

        select.dataset.bound = 'true';
        select.addEventListener('change', (e) => {
            this.onChannelSelectChange(e.target.value);
            this.syncUrlChatId(e.target.value, true);
            tgApp.hapticSelection();
        });
    },

    // ======================================================================
    // 💎 PLANES DE MEMBRESÍA DEL CANAL — listado e interacciones
    // ======================================================================
    planById(planId) {
        const id = Number(planId);
        return this._plans.list.find(p => Number(p.plan_id) === id) || null;
    },

    /** Repinta el listado desde el estado interno (o el estado "sin canal"). */
    renderPlans() {
        const plans = this._plans;
        if (!plans.channelId) {
            ui.renderChannelPlansState('idle');
            return;
        }
        ui.renderChannelPlans(plans.list, { busy: plans.busy, confirm: plans.confirm, links: plans.links, summary: plans.summary });
    },

    resetPlansState() {
        const plans = this._plans;
        clearTimeout(plans.confirmTimer);
        Object.assign(plans, { channelId: null, list: [], summary: null, busy: {}, confirm: null, confirmTimer: null, links: {}, previewId: null });
        ui.closePlanPreview();
    },

    planErrorMessage(res) {
        if (api.isEndpointMissing(res)) return ui.t('plans_err_missing');
        if (res?.__status === 403) return ui.t('plans_err_forbidden');
        if (res?.__error === 'timeout' || res?.__error === 'network') return ui.t('load_error');
        return String(res?.__error || ui.t('an_err_unavailable'));
    },

    /** Aviso flotante de una acción sobre un plan (siempre visible; un aviso nuevo sustituye al anterior). */
    planToast(tone, icon, title) {
        ui.showToast({ key: 'plan-action', force: true, icon, tone, ttl: CONFIG.PLANS.TOAST_MS, title });
    },

    planFailed(res, channelId = this._plans.channelId) {
        tgApp.hapticNotification('error');
        this.planToast('error', '⚠️', ui.tf('plans_toast_error', { error: this.planErrorMessage(res) }));
        // "Plan no encontrado": ya no existe (borrado desde el bot u otro dispositivo) → se resincroniza la lista.
        if (res?.__status === 404 && !api.isEndpointMissing(res) && channelId) {
            this.loadChannelPlans(channelId, { silent: true });
        }
        return false;
    },

    /**
     * Carga los planes del canal (GET /api/channel/{id}/plans) y los dibuja con ui.renderChannelPlans().
     * silent=true refresca sin sustituir la lista por el cargador (tras una acción o un guardado).
     * Cada petición lleva un número de secuencia: solo la más reciente pinta, y una respuesta de un canal
     * o de una sesión anteriores se descarta.
     */
    async loadChannelPlans(channelId, { silent = false } = {}) {
        const plans = this._plans;
        if (!channelId) {
            this.resetPlansState();
            ui.renderChannelPlansState('idle');
            return;
        }
        if (plans.channelId !== channelId) {
            this.resetPlansState();
            plans.channelId = channelId;
        }

        const epoch = session.epoch;
        const seq = ++plans.requestSeq;
        if (!silent || plans.list.length === 0) ui.renderChannelPlansState('loading');

        const res = await api.fetchChannelPlans(channelId);
        if (res?.__stale || epoch !== session.epoch || plans.channelId !== channelId || seq !== plans.requestSeq) return;

        if (res?.__error) {
            if (!silent || plans.list.length === 0) {
                plans.list = [];
                plans.summary = null;
                ui.renderChannelPlansState('error', this.planErrorMessage(res));
            }
            return;   // un refresco silencioso fallido no tapa una lista que ya se está mostrando
        }

        plans.list = Array.isArray(res.plans) ? res.plans : [];
        plans.summary = { total: res.total, active_count: res.active_count, subscribers: res.subscribers };

        // Un plan pausado o borrado ya no se puede comprar: su enlace generado deja de servir.
        const active = new Set(plans.list.filter(p => p.is_active).map(p => Number(p.plan_id)));
        Object.keys(plans.links).forEach(key => { if (!active.has(Number(key))) delete plans.links[key]; });
        if (plans.confirm && !plans.list.some(p => Number(p.plan_id) === Number(plans.confirm.planId))) this.dropPlanConfirm(false);

        this.renderPlans();
    },

    refreshChannelPlans() {
        const channelId = this._studio.channelId;
        if (!channelId) {
            ui.renderChannelPlansState('idle');
            return Promise.resolve();
        }
        tgApp.hapticImpact('light');
        return this.loadChannelPlans(channelId);
    },

    /** Tras una acción que cambia el catálogo: resincroniza lista y formulario del Estudio con el servidor. */
    afterPlanMutation() {
        const channelId = this._plans.channelId;
        if (!channelId) return;
        this.loadChannelPlans(channelId, { silent: true });
        if (this._studio.channelId === channelId) this.loadStudioFromServer(channelId);
    },

    // --- Confirmación inline (eliminar / difundir) ---------------------------
    askPlanConfirm(planId, action) {
        const plans = this._plans;
        clearTimeout(plans.confirmTimer);
        plans.confirm = { planId: Number(planId), action };
        tgApp.hapticNotification('warning');
        this.renderPlans();
        // Una confirmación olvidada se cancela sola: nunca queda un "¿Eliminar?" abierto indefinidamente.
        plans.confirmTimer = setTimeout(() => this.dropPlanConfirm(true), CONFIG.PLANS.CONFIRM_TIMEOUT_MS);
    },

    dropPlanConfirm(render = true) {
        const plans = this._plans;
        clearTimeout(plans.confirmTimer);
        plans.confirmTimer = null;
        if (!plans.confirm) return;
        plans.confirm = null;
        if (render) this.renderPlans();
    },

    cancelPlanConfirm() {
        tgApp.hapticSelection();
        this.dropPlanConfirm(true);
    },

    /**
     * Ejecuta una acción de un plan marcándolo "ocupado" (botones deshabilitados + spinner en la acción en curso).
     * Devuelve { res, channelId, id, valid } o null si no aplica. `valid` es false si mientras tanto se cambió
     * de canal o de sesión: el llamador no debe tocar la interfaz.
     */
    async runPlanAction(planId, action, request) {
        const plans = this._plans;
        const channelId = plans.channelId;
        const id = Number(planId);
        if (!channelId || plans.busy[id]) return null;

        const epoch = session.epoch;
        plans.busy[id] = action;
        this.renderPlans();

        const res = await request(channelId, id);

        const valid = !res?.__stale && epoch === session.epoch && plans.channelId === channelId;
        if (valid) {
            delete plans.busy[id];
            this.renderPlans();
        }
        return { res, channelId, id, valid };
    },

    // --- Acciones ------------------------------------------------------------

    /** 👁️ Previsualiza la tarjeta comercial tal como la verán los suscriptores. */
    previewPlan(planId) {
        const plan = this.planById(planId);
        if (!plan) return;
        tgApp.hapticImpact('light');
        this._plans.previewId = Number(plan.plan_id);
        ui.renderPlanPreview(plan);
    },

    closePlanPreview() {
        this._plans.previewId = null;
        ui.closePlanPreview();
    },

    /** 🔄 Activa o pausa el plan (POST .../toggle). Un plan pausado deja de poder comprarse al instante. */
    async togglePlanStatus(planId) {
        const plan = this.planById(planId);
        if (!plan) return false;
        tgApp.hapticImpact('light');

        const out = await this.runPlanAction(planId, 'toggle', (channelId, id) => api.toggleChannelPlanStatus(channelId, id));
        if (!out || !out.valid) return false;
        if (out.res?.__error) return this.planFailed(out.res, out.channelId);

        const active = out.res.is_active !== undefined ? Boolean(out.res.is_active) : out.res.new_status === 'active';
        // El plan se vuelve a resolver POR ID: mientras se esperaba al servidor una recarga pudo sustituir la lista,
        // y la referencia tomada antes de la petición ya no pertenecería a ella.
        const live = this.planById(out.id);
        if (live) {
            live.status = active ? 'active' : 'paused';
            live.is_active = active;
        }
        if (!active) delete this._plans.links[out.id];   // el enlace de un plan pausado no permite comprar
        this.renderPlans();

        tgApp.hapticNotification('success');
        this.planToast('success', active ? '🟢' : '🔴', ui.t(active ? 'plans_toast_activated' : 'plans_toast_paused'));
        this.afterPlanMutation();
        return true;
    },

    /** 📢 Publica el anuncio del plan en el canal. Pide confirmación inline: es una publicación pública. */
    async broadcastPlan(planId, confirmed = false) {
        const plan = this.planById(planId);
        if (!plan) return false;
        if (!plan.is_active) {
            tgApp.hapticNotification('warning');
            this.planToast('warn', '⚠️', ui.t('plans_err_inactive'));
            return false;
        }
        if (!confirmed) {
            this.askPlanConfirm(planId, 'broadcast');
            return false;
        }

        this.dropPlanConfirm(false);
        tgApp.hapticImpact('medium');
        const out = await this.runPlanAction(planId, 'broadcast', (channelId, id) => api.broadcastChannelPlan(channelId, id, { lang: state.currentLang }));
        if (!out || !out.valid) return false;
        if (out.res?.__error) return this.planFailed(out.res, out.channelId);

        tgApp.hapticNotification('success');
        this.planToast('success', '📢', ui.t('plans_toast_broadcast'));
        return true;
    },

    /** 🗑️ Elimina el plan (DELETE). Pide confirmación inline: la acción no se puede deshacer. */
    async deletePlan(planId, confirmed = false) {
        const plan = this.planById(planId);
        if (!plan) return false;
        if (!confirmed) {
            this.askPlanConfirm(planId, 'delete');
            return false;
        }

        this.dropPlanConfirm(false);
        tgApp.hapticImpact('medium');
        const out = await this.runPlanAction(planId, 'delete', (channelId, id) => api.deleteChannelPlan(channelId, id));
        if (!out || !out.valid) return false;
        if (out.res?.__error) return this.planFailed(out.res, out.channelId);

        const plans = this._plans;
        plans.list = plans.list.filter(p => Number(p.plan_id) !== out.id);
        delete plans.links[out.id];
        if (plans.summary) {
            plans.summary = { ...plans.summary, total: plans.list.length, active_count: plans.list.filter(p => p.is_active).length };
        }
        if (plans.previewId === out.id) this.closePlanPreview();
        this.renderPlans();

        tgApp.hapticNotification('success');
        this.planToast('success', '🗑️', ui.t('plans_toast_deleted'));
        this.afterPlanMutation();
        return true;
    },

    /** Enlace de compra calculado en el cliente (mismo formato que el servidor). Solo se usa si el backend no expone el endpoint. */
    localPurchaseLink(planId, channelId) {
        return `https://t.me/${CONFIG.BOT_USERNAME}?start=chanplan_${planId}_${channelId}`;
    },

    /**
     * 🔗 Genera (POST .../invite-link) y copia el enlace de compra del plan. Con el enlace ya generado,
     * el mismo botón solo lo copia. Quien paga con Stars recibe automáticamente su acceso VIP de un solo uso.
     */
    async generateInviteLink(planId) {
        const plan = this.planById(planId);
        if (!plan) return false;
        if (!plan.is_active) {
            tgApp.hapticNotification('warning');
            this.planToast('warn', '⚠️', ui.t('plans_err_inactive'));
            return false;
        }
        if (this._plans.links[plan.plan_id]) return await this.copyPlanLink(plan.plan_id);

        tgApp.hapticImpact('light');
        const out = await this.runPlanAction(planId, 'link', (channelId, id) => api.generatePlanInviteLink(channelId, id));
        if (!out || !out.valid) return false;

        let link = out.res?.link;
        let local = false;
        if (out.res?.__error) {
            if (!api.isEndpointMissing(out.res)) return this.planFailed(out.res, out.channelId);
            link = this.localPurchaseLink(plan.plan_id, out.channelId);
            local = true;
        }
        if (typeof link !== 'string' || !/^https:\/\/t\.me\//i.test(link)) {
            return this.planFailed({ __error: ui.t('an_err_unavailable'), __status: 502 }, out.channelId);
        }

        this._plans.links[plan.plan_id] = link;
        this.renderPlans();

        const copied = await this.copyToClipboard(link);
        tgApp.hapticNotification('success');
        this.planToast('success', '🔗', ui.t(local ? 'plans_toast_link_local' : (copied ? 'plans_toast_link_copied' : 'plans_toast_link_ready')));
        return true;
    },

    /** Copia el enlace ya generado de un plan (botón "Copiar" de la tarjeta). */
    async copyPlanLink(planId) {
        const link = this._plans.links[Number(planId)];
        if (!link) return false;
        const copied = await this.copyToClipboard(link);
        if (copied) {
            tgApp.hapticNotification('success');
            this.planToast('success', '🔗', ui.t('plans_toast_link_copied'));
        } else {
            tgApp.hapticNotification('warning');
            this.planToast('warn', '🔗', ui.t('plans_toast_copy_failed'));
        }
        return copied;
    },

    /**
     * Copia sin diálogos. El portapapeles asíncrono puede rechazar la llamada si la activación del usuario
     * caducó mientras esperaba al servidor (Safari / WebView de iOS): se intenta el método clásico y, si
     * también falla, el enlace queda visible en la tarjeta para copiarlo con un toque en "Copiar".
     */
    async copyToClipboard(text) {
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
                return true;
            }
        } catch (err) { /* se prueba el método clásico */ }
        try {
            const area = document.createElement('textarea');
            area.value = text;
            area.setAttribute('readonly', '');
            area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
            document.body.appendChild(area);
            if (area.select) area.select();
            const done = typeof document.execCommand === 'function' ? document.execCommand('copy') : false;
            area.remove();
            return Boolean(done);
        } catch (err) {
            return false;
        }
    },

    // ======================================================================
    // 🎬 ESTUDIO DE CANALES — guardado reactivo (enlace VIP · tarifa en Stars · días)
    // ======================================================================
    bindChannelStudio() {
        Object.values(ui.STUDIO_FIELDS).forEach(id => {
            const el = document.getElementById(id);
            if (!el || el.dataset.studioBound) return;
            el.dataset.studioBound = 'true';
            el.addEventListener('input', () => this.onStudioInput());
            // Al confirmar el valor (salir del campo) se guarda sin esperar al temporizador.
            el.addEventListener('change', () => this.saveChannelStudio({ auto: true }));
        });
    },

    applyStudioValidation(result) {
        const map = ui.STUDIO_FIELDS;
        Object.entries(map).forEach(([field, id]) => {
            const err = result.errors[field];
            ui.setFieldError(id, err ? ui.tf(err.key, err.vars) : '');
        });
    },

    onStudioInput() {
        ui.updatePromoCounter();
        const studio = this._studio;
        if (!studio.channelId) {
            ui.setStudioStatus('invalid', ui.t('studio_pick_channel'));
            return;
        }
        studio.dirty = true;

        const result = validateStudioForm(ui.readStudioForm());
        this.applyStudioValidation(result);
        clearTimeout(studio.timer);
        studio.timer = null;

        if (!result.valid) {
            ui.setStudioStatus('invalid', ui.t('studio_status_invalid'));
            return;
        }
        ui.setStudioStatus('dirty', ui.t('studio_status_dirty'));
        // Con un guardado en curso no se programa otro: al terminar se compara el formulario y se reprograma si cambió.
        if (studio.saving) return;
        studio.timer = setTimeout(() => {
            studio.timer = null;
            this.saveChannelStudio({ auto: true });
        }, CONFIG.STUDIO.AUTOSAVE_MS);
    },

    /**
     * Guarda enlace VIP, tarifa en Stars y duración con api.updateChatSettings().
     *  • auto=true: disparado por el guardado reactivo; auto=false: botón "Guardar Ajustes".
     *  • Valida antes de enviar: nada inválido llega al servidor.
     *  • Tras guardar lee el dashboard del canal y confirma que el servidor refleja lo enviado.
     */
    async saveChannelStudio({ auto = false } = {}) {
        const studio = this._studio;
        const channelId = studio.channelId || document.getElementById('channel-owner-select')?.value || null;
        if (!channelId) {
            ui.showToast({ key: 'studio-pick', force: true, icon: '⚠️', tone: 'warn', ttl: 3000, title: ui.t('studio_pick_channel') });
            return false;
        }

        clearTimeout(studio.timer);
        studio.timer = null;

        const result = validateStudioForm(ui.readStudioForm());
        this.applyStudioValidation(result);
        if (!result.valid) {
            ui.setStudioStatus('invalid', ui.t('studio_status_invalid'));
            if (!auto) tgApp.hapticNotification('error');
            return false;
        }
        if (auto && !studio.dirty) return true;   // 'change' sin edición real: nada que guardar
        if (studio.saving) return false;          // el guardado en curso reprograma al terminar

        const payload = result.values;            // exactamente lo validado y normalizado
        const epoch = session.epoch;
        studio.saving = true;
        ui.setStudioBusy(true);
        ui.setStudioStatus('saving', ui.t('studio_status_saving'));

        const res = await api.updateChatSettings(channelId, payload);

        studio.saving = false;
        ui.setStudioBusy(false);
        if (res?.__stale || epoch !== session.epoch) return false;

        // El operador cambió de canal mientras se guardaba: no se toca la interfaz del canal nuevo.
        if (studio.channelId !== channelId) return !res?.__error;

        if (res?.__error) {
            // v8.2: el servidor verifica el chat destino en Telegram; sus errores llegan como "campo: motivo".
            const fieldMatch = /^(broadcast_target|broadcast_interval|promo_text):\s*(.+)$/.exec(String(res.__error));
            if (fieldMatch && (res.__status === 422 || res.__status === 403)) {
                ui.setFieldError(ui.STUDIO_FIELDS[fieldMatch[1]], fieldMatch[2]);
                ui.setStudioStatus('invalid', ui.t('studio_status_invalid'));
            } else {
                ui.setStudioStatus('error', res.__status === 403
                    ? ui.t('studio_status_forbidden')
                    : ui.tf('studio_status_error', { error: res.__error }));
            }
            tgApp.hapticNotification('error');
            return false;
        }

        studio.last = payload;
        if (res?.broadcast) {
            studio.broadcast = this.broadcastFromServer(res.broadcast);
            ui.setBroadcastStatus(studio.broadcast);
        }
        const current = validateStudioForm(ui.readStudioForm());
        const sameAsSent = current.valid
            && Object.keys(payload).every(key => current.values[key] === payload[key]);

        if (!sameAsSent) {
            // El operador siguió escribiendo durante el guardado: sus cambios nuevos se guardan a continuación.
            studio.dirty = true;
            if (current.valid) {
                ui.setStudioStatus('dirty', ui.t('studio_status_dirty'));
                studio.timer = setTimeout(() => {
                    studio.timer = null;
                    this.saveChannelStudio({ auto: true });
                }, CONFIG.STUDIO.AUTOSAVE_MS);
            }
            return true;
        }

        studio.dirty = false;
        ui.fillStudioForm(payload);   // muestra el valor normalizado (p. ej. @alias → https://t.me/alias)
        const time = new Date().toLocaleTimeString(state.currentLang === 'es' ? 'es-CO' : 'en-US', { hour12: false });
        ui.setStudioStatus('saved', ui.tf('studio_status_saved_unverified', { time }));
        if (!auto) tgApp.hapticNotification('success');

        await this.verifyStudioSaved(channelId, payload, time);
        this.loadChannelPlans(channelId, { silent: true });   // la lista de planes refleja lo recién guardado
        return true;
    },

    /** Relee el dashboard del canal y confirma que el servidor devuelve lo que se acaba de guardar. */
    async verifyStudioSaved(channelId, payload, time) {
        const studio = this._studio;
        const epoch = session.epoch;
        const data = await api.fetchChatDashboard(channelId);
        if (!data || data.__error || data.__stale) return;   // sin lectura no hay veredicto: se queda "Guardado ✓"
        if (epoch !== session.epoch || studio.channelId !== channelId || studio.dirty) return;

        // La difusión solo se verifica si el backend la expone en el dashboard.
        const broadcast = this.broadcastFromServer(data);
        if (!broadcast) return;
        studio.broadcast = broadcast;
        ui.setBroadcastStatus(broadcast);
        const matches = broadcast.broadcast_target === payload.broadcast_target
            && broadcast.broadcast_interval === payload.broadcast_interval
            && broadcast.promo_text === payload.promo_text;
        if (matches) {
            ui.setStudioStatus('saved', ui.tf('studio_status_saved', { time }));
        } else {
            const values = `${broadcast.broadcast_target || '—'} · ${broadcast.broadcast_interval} h`;
            ui.setStudioStatus('mismatch', ui.tf('studio_status_mismatch', { values }));
        }
    },

    /** Alias de compatibilidad: versiones anteriores del HTML llamaban a openChannelStudio() (Telegram cachea agresivamente). */
    async openChannelStudio() {
        return await this.saveChannelStudio({ auto: false });
    },

    // ======================================================================
    // ➕ GENERADOR DE PLANES — CRUD real sobre channel_plans (v8.3)
    // ======================================================================
    bindPlanForm() {
        Object.values(ui.PLAN_FORM_FIELDS).forEach(id => {
            const el = document.getElementById(id);
            if (!el || el.dataset.planBound) return;
            el.dataset.planBound = 'true';
            el.addEventListener('input', () => {
                ui.setFieldError(id, '');
                if (id === ui.PLAN_FORM_FIELDS.promo_text) ui.updatePlanPromoCounter();
            });
        });
    },

    applyPlanFormValidation(result) {
        Object.entries(ui.PLAN_FORM_FIELDS).forEach(([field, id]) => {
            const err = result.errors[field];
            ui.setFieldError(id, err ? ui.tf(err.key, err.vars) : '');
        });
    },

    /** Valida el formulario y devuelve los valores o null (marcando los errores). */
    readValidPlanForm() {
        const result = validatePlanForm(ui.readPlanForm());
        this.applyPlanFormValidation(result);
        if (!result.valid) {
            tgApp.hapticNotification('error');
            this.planToast('error', '⚠️', ui.t('pf_toast_invalid'));
            return null;
        }
        return result.values;
    },

    /**
     * 💾 Crear y Activar: POST /api/channel/{id}/plan/create. Spinner durante la petición, toast háptico
     * verde, formulario vacío y lista inferior refrescada. Devuelve la respuesta del servidor o null.
     */
    async createPlanFromForm() {
        const channelId = this._studio.channelId;
        if (!channelId) {
            tgApp.hapticNotification('warning');
            this.planToast('warn', '⚠️', ui.t('studio_pick_channel'));
            return null;
        }
        if (this._planForm.busy) return null;
        const values = this.readValidPlanForm();
        if (!values) return null;

        const epoch = session.epoch;
        this._planForm.busy = true;
        ui.setPlanFormBusy(true);
        tgApp.hapticImpact('medium');
        try {
            const res = await api.createChannelPlan(channelId, values);
            if (res?.__stale || epoch !== session.epoch || this._studio.channelId !== channelId) return null;
            if (res?.__error) {
                const match = /^(plan_name|stars_price|duration_days|target_link|promo_text):\s*(.+)$/.exec(String(res.__error));
                if (match && res.__status === 422) ui.setFieldError(ui.PLAN_FORM_FIELDS[match[1]], match[2]);
                tgApp.hapticNotification('error');
                this.planToast('error', '⚠️', ui.tf('pf_toast_error', { error: this.planErrorMessage(res) }));
                return null;
            }
            const ready = Boolean(res.delivery?.ready);
            tgApp.hapticNotification(ready ? 'success' : 'warning');
            ui.showToast({
                key: 'plan-action', force: true, icon: '💎', tone: ready ? 'success' : 'warn', ttl: 5200,
                title: ui.tf('pf_toast_created', { name: res.plan?.name || values.plan_name }),
                body: ui.t(ready ? 'pf_toast_created_body' : 'pf_toast_created_warn')
            });
            ui.resetPlanForm();
            this._planForm.lastCreated = res;
            ui.renderPlanFormResult(res);
            if (res.plan && res.purchase_link) this._plans.links[res.plan.plan_id] = res.purchase_link;
            await this.loadChannelPlans(channelId, { silent: true });
            return res;
        } finally {
            this._planForm.busy = false;
            ui.setPlanFormBusy(false);
        }
    },

    /** 👁️ Previsualizar Tarjeta: la misma tarjeta que el listado (texto con ui.safeTelegramHtml). */
    previewPlanForm() {
        const result = validatePlanForm(ui.readPlanForm());
        this.applyPlanFormValidation(result);
        const raw = ui.readPlanForm();
        tgApp.hapticImpact('light');
        ui.renderPlanPreview({
            name: result.values.plan_name || raw.plan_name || '—',
            duration_days: result.values.duration_days ?? (parseInt(raw.duration_days, 10) || 0),
            stars_price: result.values.stars_price ?? (parseInt(raw.stars_price, 10) || 0),
            promo_text: result.values.promo_text ?? raw.promo_text,
            target_link: result.values.target_link || '',
            has_media: false,
            media_type: ''
        });
    },

    /**
     * 📢 Difundir al Canal: si el formulario tiene un plan nuevo, se crea y activa primero (con confirmación)
     * y después se publica con su botón de compra. Con el formulario vacío se difunde el último plan creado.
     */
    async broadcastPlanForm() {
        const channelId = this._studio.channelId;
        if (!channelId) {
            tgApp.hapticNotification('warning');
            this.planToast('warn', '⚠️', ui.t('studio_pick_channel'));
            return false;
        }
        if (this._planForm.busy) return false;

        const raw = ui.readPlanForm();
        const formEmpty = !String(raw.plan_name || '').trim() && !String(raw.promo_text || '').trim() && !String(raw.target_link || '').trim();
        let plan = formEmpty ? this._planForm.lastCreated?.plan : null;

        if (!plan) {
            const values = this.readValidPlanForm();
            if (!values) return false;
            if (!await this.confirmAction(ui.tf('pf_confirm_broadcast_new', { name: values.plan_name }))) return false;
            const created = await this.createPlanFromForm();
            if (!created?.plan) return false;
            plan = created.plan;
        } else if (!await this.confirmAction(ui.tf('pf_confirm_broadcast', { name: plan.name }))) {
            return false;
        }

        if (this._plans.channelId !== channelId) await this.loadChannelPlans(channelId, { silent: true });
        if (!this.planById(plan.plan_id)) {
            await this.loadChannelPlans(channelId, { silent: true });
        }
        const epoch = session.epoch;
        this._planForm.busy = true;
        ui.setButtonBusy('pf-broadcast-btn', true);
        try {
            const res = await api.broadcastChannelPlan(channelId, plan.plan_id, { lang: state.currentLang });
            if (res?.__stale || epoch !== session.epoch) return false;
            if (res?.__error) return this.planFailed(res, channelId);
            tgApp.hapticNotification('success');
            this.planToast('success', '📢', ui.t('plans_toast_broadcast'));
            return true;
        } finally {
            this._planForm.busy = false;
            ui.setButtonBusy('pf-broadcast-btn', false);
        }
    },

    /** 🗑️ Limpiar Formulario. */
    clearPlanForm() {
        if (this._planForm.busy) return;
        ui.resetPlanForm();
        this._planForm.lastCreated = null;
        tgApp.hapticImpact('light');
        this.planToast('info', '🗑️', ui.t('pf_toast_cleared'));
    },

    // ======================================================================
    // 📡 DIFUSIÓN PERSONALIZADA — acciones rápidas del Estudio (v8.2)
    // ======================================================================
    /** Nombre legible del destino de la difusión (destino configurado o el propio canal). */
    broadcastTargetLabel(values) {
        if (values?.broadcast_target) return values.broadcast_target;
        const channelId = this._studio.channelId;
        const channel = state.data.channels.find(c => String(c.id) === String(channelId));
        return channel ? channel.title : ui.t('bc_preview_target_self');
    },

    /** 👁️ Vista previa: el texto se formatea con ui.safeTelegramHtml (la misma gramática que publica el bot). */
    previewCustomBroadcast() {
        const result = validateStudioForm(ui.readStudioForm());
        this.applyStudioValidation(result);
        const promo = result.values.promo_text ?? String(ui.readStudioForm().promo_text || '').trim();
        if (!promo) {
            tgApp.hapticNotification('warning');
            ui.setFieldError(ui.STUDIO_FIELDS.promo_text, ui.t('bc_err_empty'));
            this.consoleToast('warn', '✍️', ui.t('bc_err_empty'));
            return false;
        }
        tgApp.hapticImpact('light');
        this._lastBroadcastPreview = {
            promoText: promo,
            targetLabel: result.values.broadcast_target || '',
            interval: result.values.broadcast_interval || (CONFIG.STUDIO.DEFAULT_INTERVAL ?? 12),
            scheduled: Boolean(result.values.broadcast_target)
        };
        ui.renderBroadcastPreview(this._lastBroadcastPreview);
        return true;
    },

    closeBroadcastPreview() {
        this._lastBroadcastPreview = null;
        ui.closeBroadcastPreview();
    },

    /** Espera a que termine un guardado en curso del Estudio (máx. ~12 s). */
    async waitStudioIdle() {
        const started = Date.now();
        while (this._studio.saving && Date.now() - started < 12000) {
            await new Promise(resolve => setTimeout(resolve, 150));
        }
        return !this._studio.saving;
    },

    /**
     * 📢 Difundir ahora: guarda primero lo que hay en el formulario (el servidor publica la configuración
     * GUARDADA, la misma que muestra la vista previa) y después publica en el destino o en el propio canal.
     */
    async broadcastCustomBroadcast() {
        const studio = this._studio;
        const channelId = studio.channelId;
        if (!channelId) {
            tgApp.hapticNotification('warning');
            this.consoleToast('warn', '⚠️', ui.t('studio_pick_channel'));
            return false;
        }
        if (this._broadcastBusy) return false;

        const result = validateStudioForm(ui.readStudioForm());
        this.applyStudioValidation(result);
        if (!result.valid) {
            tgApp.hapticNotification('error');
            this.consoleToast('error', '⚠️', ui.t('bc_toast_save_first'));
            return false;
        }
        if (!result.values.promo_text) {
            tgApp.hapticNotification('warning');
            ui.setFieldError(ui.STUDIO_FIELDS.promo_text, ui.t('bc_err_empty'));
            this.consoleToast('warn', '✍️', ui.t('bc_err_empty'));
            return false;
        }

        const targetLabel = this.broadcastTargetLabel(result.values);
        if (!await this.confirmAction(ui.tf('bc_confirm_send', { target: targetLabel }))) return false;

        const epoch = session.epoch;
        this._broadcastBusy = true;
        ui.setBroadcastBusy(true);
        tgApp.hapticImpact('medium');
        try {
            await this.waitStudioIdle();
            if (epoch !== session.epoch || studio.channelId !== channelId) return false;
            const unsaved = studio.dirty || studio.timer || !studio.last
                || Object.keys(result.values).some(key => studio.last[key] !== result.values[key]);
            if (unsaved) {
                const saved = await this.saveChannelStudio({ auto: false });
                if (!saved || epoch !== session.epoch || studio.channelId !== channelId) {
                    if (epoch === session.epoch) this.consoleToast('error', '⚠️', ui.t('bc_toast_save_first'));
                    return false;
                }
            }

            const res = await api.sendCustomBroadcast(channelId, { lang: state.currentLang });
            if (res?.__stale || epoch !== session.epoch || studio.channelId !== channelId) return false;
            if (res?.__error) {
                tgApp.hapticNotification('error');
                this.consoleToast('error', '⚠️', ui.tf('bc_toast_error', { error: this.planErrorMessage(res) }));
                return false;
            }
            tgApp.hapticNotification('success');
            this.consoleToast('success', '📢', ui.tf('bc_toast_sent', { target: res.target_label || targetLabel }));
            this.closeBroadcastPreview();
            this.loadStudioFromServer(channelId);
            return true;
        } finally {
            this._broadcastBusy = false;
            ui.setBroadcastBusy(false);
        }
    },

    /** 🗑️ Limpia la difusión (destino, intervalo y texto) y lo guarda: desactiva la difusión automática. */
    async clearBroadcastForm() {
        const studio = this._studio;
        if (!await this.confirmAction(ui.t('bc_confirm_clear'))) return false;
        const defaults = this.studioDefaults();
        const fields = ui.STUDIO_FIELDS;
        [fields.broadcast_target, fields.broadcast_interval, fields.promo_text].forEach(id => {
            const el = document.getElementById(id);
            if (el && document.activeElement === el) el.blur();
            ui.setFieldError(id, '');
        });
        ui.fillStudioForm({
            broadcast_target: defaults.broadcast_target,
            broadcast_interval: defaults.broadcast_interval,
            promo_text: defaults.promo_text
        });
        this.closeBroadcastPreview();
        tgApp.hapticImpact('medium');
        if (!studio.channelId) {
            this.consoleToast('info', '🗑️', ui.t('bc_toast_cleared'));
            return true;
        }
        studio.dirty = true;
        const saved = await this.saveChannelStudio({ auto: false });
        if (saved) this.consoleToast('success', '🗑️', ui.t('bc_toast_cleared'));
        return saved;
    },

    async loadChannels() {
        const data = await api.fetchChannels();
        if (data?.__stale) return;
        if (data?.__error) {
            ui.renderChatListError('channels-list', ui.t('load_error'), 'loadChannels');
            return;
        }
        this.applyChannels((data && data.channels) || []);
    },

    /** Pinta la lista de canales y puebla #channel-owner-select (desde GET /channels o POST /sync-chats). */
    applyChannels(channels) {
        state.data.channels = Array.isArray(channels) ? channels : [];
        ui.renderChatList('channels-list', state.data.channels, ui.t('no_channels'));
        ui.populateSelect('channel-owner-select', state.data.channels, ui.t('no_channels'));

        this.bindChannelSelectListener();

        const select = document.getElementById('channel-owner-select');
        if (select && select.value) {
            this.onChannelSelectChange(select.value);
        } else if (select && select.options.length > 1) {
            // Autoselección de cortesía del primer canal. No debe secuestrar la comunidad que el operador
            // abrió por deep link (?chat_id= / startapp=): onChannelSelectChange sobrescribe selectedChatId.
            const openedChat = state.selectedChatId;
            select.selectedIndex = 1;
            this.onChannelSelectChange(select.value);
            if (openedChat && String(openedChat) !== String(select.value)) {
                state.selectedChatId = openedChat;
            }
        }
    },

    async loadGroups() {
        const data = await api.fetchGroups();
        if (data?.__stale) return;
        if (data?.__error) {
            ui.renderChatListError('groups-list', ui.t('load_error'), 'loadGroups');
            return;
        }
        this.applyGroups((data && data.groups) || []);
    },

    /** Pinta la lista de comunidades y puebla #group-owner-select y #analytics-chat-select. */
    applyGroups(groups) {
        state.data.groups = Array.isArray(groups) ? groups : [];
        ui.renderChatList('groups-list', state.data.groups, ui.t('no_groups'));
        ui.populateSelect('group-owner-select', state.data.groups, ui.t('no_groups'));
        ui.populateSelect('analytics-chat-select', state.data.groups, ui.t('no_groups'), 'group');
        const selected = document.getElementById('group-owner-select')?.value || '';
        if (!selected) {
            state.securitySwitches = null;
            state.switchesChatId = null;
            this._chatConfig.seq += 1;
            Object.assign(this._chatConfig, { chatId: null, data: null, dirty: {}, saving: {}, switchBusy: {} });
            ui.setConsoleState('idle');
        }
    },

    async loadSubscribers() {
        const data = await api.fetchSubscribers();
        if (data?.__stale) return;
        if (data?.__error) {
            const el = document.getElementById('watchdog-list');
            if (el) el.innerHTML = `<div class="glass-panel p-6 text-center text-xs text-rose-400 font-mono">⚠️ ${ui.t('load_error')}</div>`;
            return;
        }
        state.data.subscribers = (data && data.subscribers) || [];
        ui.renderSubscriberList(state.data.subscribers);
    },

    renewLicense(chatId) {
        this.switchTab('plans');
    },

    /**
     * Selecciona una comunidad: carga su panel y arranca la analítica en vivo.
     *  a) cierra el WebSocket anterior si el operador cambió de comunidad,
     *  b) abre el cliente BunkerWebSocketClient de la nueva (sin esperar al REST),
     *  c) el evento analytics_snapshot repinta el panel sin recargar la página,
     *  d) pide la analítica por REST para pintar de inmediato mientras el socket conecta.
     * opts.tab: pestaña de destino (por defecto 'bot-settings'; 'analytics' desde los botones del bot).
     */
    async configureChat(chatId, opts = {}) {
        if (!chatId) return;
        chatId = String(chatId);

        const epoch = ++state.chatEpoch;
        state.selectedChatId = chatId;

        document.querySelectorAll('#group-owner-select, #analytics-chat-select').forEach(sel => {
            sel.value = chatId;
        });

        // La analítica en vivo solo existe para grupos: los canales no generan mensajes rastreables.
        const isChannel = state.data.channels.some(c => String(c.id) === chatId);

        // Consola de programación (v8.3): hasta leer la configuración de ESTA comunidad nada es editable.
        if (isChannel) {
            this._chatConfig.seq += 1;
            Object.assign(this._chatConfig, { chatId: null, data: null, dirty: {}, saving: {}, switchBusy: {} });
            ui.setConsoleState('idle');
        }
        const hasLiveForChat = Boolean(
            state.wsClient && state.wsClient.chatId === chatId && state.wsClient.status !== 'denied'
        );

        this.syncUrlChatId(chatId, isChannel);

        if (isChannel) {
            this.disconnectLiveRadar();
        } else if (!hasLiveForChat) {
            this.connectLiveRadar(chatId);
        }

        // Desde el botón del bot el operador espera ver la analítica ya: se muestra el cargador al instante.
        if (opts.tab === 'analytics') this.switchTab('analytics');

        const tasks = [
            isChannel ? Promise.resolve() : this.loadChatConfiguration(chatId, { keepDirty: this._chatConfig.chatId === chatId }),
            this.loadChatDashboard(chatId),
            this.loadChatStats(chatId),
            this.loadChatAdminStats(chatId),
            this.loadChatTopUsers(chatId)
        ];
        if (!isChannel) tasks.push(this.loadCommunityAnalytics(chatId));
        await Promise.all(tasks);

        if (epoch !== state.chatEpoch) return;   // el operador cambió de comunidad mientras cargaba
        if (opts.tab !== 'analytics') this.switchTab(opts.tab || 'bot-settings');
    },

    openAnalytics(chatId) {
        if (!chatId) {
            this.disconnectLiveRadar();
            this.switchTab('analytics');
            return Promise.resolve();
        }
        return this.configureChat(chatId, { tab: 'analytics' });
    },

    // ======================================================================
    // 📡 RADAR EN VIVO — WebSocket + analítica unificada
    // ======================================================================
    connectLiveRadar(chatId) {
        const switching = Boolean(state.wsClient);   // ¿se está pasando de una comunidad a otra?
        this.disconnectLiveRadar();                  // una sola conexión: se cierra la de la comunidad anterior

        const client = new BunkerWebSocketClient(chatId);
        state.wsClient = client;
        const isCurrent = () => state.wsClient === client;
        let wasLive = false;

        ui.setRadarTarget(this.communityTitle(chatId));
        client.on('status', ({ status }) => {
            if (!isCurrent()) return;
            if (status === 'live') wasLive = true;
            // Al cambiar de comunidad el radar se re-enlaza: "Reconectando 🟡" hasta el saludo (hello) del
            // servidor, que ya autenticó la sesión y la comunidad; solo entonces "Conectado 🟢".
            ui.setWsStatus(status === 'connecting' && switching && !wasLive ? 'reconnecting' : status);
        });
        client.on('analytics_snapshot', (data) => { if (isCurrent()) this.applyAnalyticsSnapshot(data, 'ws'); });
        client.on('initial_state', (data) => { if (isCurrent()) state.liveTelemetry = data; });
        client.on('state_refresh', (data) => { if (isCurrent()) state.liveTelemetry = data; });
        client.on('message', (data) => { if (isCurrent()) this.onLiveMessage(data); });
        client.on('level_up', (data) => { if (isCurrent()) this.onLiveLevelUp(data); });
        client.on('voice_presence', (data) => { if (isCurrent()) this.onVoicePresence(data); });
        client.on('voice_call_started', (data) => { if (isCurrent()) this.onVoiceCall(true, data); });
        client.on('voice_call_ended', (data) => { if (isCurrent()) this.onVoiceCall(false, data); });
        client.on('stars_payment', (data) => { if (isCurrent()) this.onStarsPayment(data); });
        client.on('settings_updated', (data) => {
            if (!isCurrent()) return;
            this.scheduleDashboardReload(chatId);
            this.onRemoteSettingsUpdated(chatId, data);
        });
        client.on('gap', () => { if (isCurrent()) this.scheduleAnalyticsRefresh(500); });
        client.on('auth_failed', ({ code }) => { if (isCurrent()) this.onRadarAuthFailed(code); });
        client.on('reconnected', () => {
            if (!isCurrent()) return;
            // Tras una caída se pierden eventos, pero el servidor envía un analytics_snapshot nuevo tras cada
            // "hello": ese snapshot reconcilia el panel sin pedir nada. Volver del segundo plano no merece aviso.
            if (Date.now() < this._silentUntil) return;
            ui.showToast({ key: 'radar-reconnected', icon: '🟢', tone: 'success', ttl: 2500, title: ui.t('toast_reconnected') });
        });

        // Red de seguridad: un snapshot cada 5 min cubre el cambio de día aunque no haya mensajes.
        this._idleRefreshTimer = setInterval(() => { if (isCurrent()) client.requestAnalytics(); }, 300000);

        client.connect();
        return client;
    },

    disconnectLiveRadar() {
        clearTimeout(this._refreshTimer);
        clearTimeout(this._dashboardTimer);
        clearTimeout(this._hiddenTimer);
        clearInterval(this._idleRefreshTimer);
        this._refreshTimer = this._dashboardTimer = this._hiddenTimer = this._idleRefreshTimer = null;
        this._silentUntil = 0;

        const client = state.wsClient;
        state.wsClient = null;               // antes de cerrar: el manejador "status" ya no pintará nada
        if (client) client.close();

        state.liveAnalytics = null;
        state.liveTelemetry = null;
        state.liveVoice = { active: false, callId: null, participants: 0, mics: 0 };
        ui.clearToasts();
        ui.setWsStatus('idle');
        ui.setRadarTarget('');
        ui.resetAnalyticsView();
        ui.renderVoiceCard();
    },

    analyticsErrorMessage(res) {
        if (res.__status === 403) return ui.t('an_err_forbidden');
        if (res.__status === 503 || res.__status === 504) return ui.t('an_err_unavailable');
        if (res.__error === 'timeout' || res.__error === 'network') return ui.t('load_error');
        return ui.t('an_err_unavailable');
    },

    /** GET /api/community/{chat_id}/analytics. fresh=true se salta la caché del servidor. */
    async loadCommunityAnalytics(chatId, { fresh = false } = {}) {
        const epoch = state.chatEpoch;
        ui.setAnalyticsLoading(true);

        const data = await api.fetchCommunityAnalytics(chatId, fresh);
        if (data?.__stale) return;
        if (epoch !== state.chatEpoch || String(state.selectedChatId) !== String(chatId)) return;   // respuesta obsoleta

        if (data?.__error) {
            // Con datos ya en pantalla, un fallo puntual de refresco no los tapa con un error.
            if (!state.liveAnalytics) {
                ui.renderAnalyticsError(this.analyticsErrorMessage(data), data.__status !== 403);
            }
            return;
        }
        this.applyAnalyticsSnapshot(data, 'rest');
    },

    /** Punto único de entrada de snapshots (REST o WebSocket). Descarta los obsoletos o de otra comunidad. */
    applyAnalyticsSnapshot(data, source = 'ws') {
        if (!data || typeof data !== 'object' || !data.summary) return false;
        if (data.chat_id !== undefined && String(data.chat_id) !== String(state.selectedChatId)) return false;

        const current = state.liveAnalytics;
        if (current && Number(data.generated_at) < Number(current.generated_at)) return false;   // REST lento tras un snapshot más nuevo

        state.liveAnalytics = data;
        ui.renderAnalytics(data);
        return true;
    },

    refreshAnalytics() {
        if (!state.selectedChatId) return Promise.resolve();
        tgApp.hapticImpact('light');
        return this.loadCommunityAnalytics(state.selectedChatId, { fresh: true });
    },

    onRadarAuthFailed(code) {
        if (state.liveAnalytics) return;
        ui.renderAnalyticsError(ui.t(code === 4403 ? 'an_err_forbidden' : 'login_expired'), false);
    },

    /** Pide un snapshot nuevo tras actividad en vivo, con un mínimo entre peticiones. */
    scheduleAnalyticsRefresh(delayMs = null) {
        if (!state.wsClient || this._refreshTimer) return;
        const minGap = CONFIG.ANALYTICS?.REFRESH_MIN_MS || 20000;
        const sinceLast = Date.now() - (this._lastLiveRefresh || 0);
        const wait = delayMs !== null ? delayMs : Math.max(1000, minGap - sinceLast);

        this._refreshTimer = setTimeout(() => {
            this._refreshTimer = null;
            this._lastLiveRefresh = Date.now();
            state.wsClient?.requestAnalytics();
        }, wait);
    },

    scheduleDashboardReload(chatId) {
        clearTimeout(this._dashboardTimer);
        this._dashboardTimer = setTimeout(() => {
            if (String(state.selectedChatId) === String(chatId)) this.loadChatDashboard(chatId);
        }, 500);
    },

    /** Destello de KPI como máximo una vez cada 400 ms (comunidades con decenas de mensajes por segundo). */
    flashThrottled(id) {
        const now = Date.now();
        if (now - (this._lastFlash || 0) < 400) return;
        this._lastFlash = now;
        ui.flash(id);
    },

    // --- Eventos en vivo ---------------------------------------------------
    onLiveMessage(d) {
        if (!d) return;
        const kind = d.k || 'other';
        const name = d.n || `ID ${d.u}`;

        // Contadores optimistas: el siguiente snapshot los sustituye por el valor real.
        const summary = state.liveAnalytics?.summary;
        if (summary) {
            summary.messages_today = (summary.messages_today || 0) + 1;
            summary.messages_30d = (summary.messages_30d || 0) + 1;
            ui.renderMessagesToday(summary);
            this.flashThrottled('an-kpi-messages-today');
        }

        ui.pushFeedItem({ icon: ui.kindIcon(kind), key: 'feed_message', vars: { name, kind } });
        ui.notifyLiveMessage({ name, kind });
        this.scheduleAnalyticsRefresh();
    },

    onLiveLevelUp(d) {
        if (!d) return;
        const name = d.n || `ID ${d.u}`;
        ui.pushFeedItem({ icon: '🏆', key: 'feed_level_up', vars: { name, level: d.l } });
        ui.notifyLevelUp({ name, level: d.l, userId: d.u });
        tgApp.hapticNotification('success');
        this.scheduleAnalyticsRefresh(1500);   // el cuadro de honor cambia
    },

    onVoicePresence(d) {
        if (!d) return;
        state.liveVoice = {
            ...state.liveVoice,
            active: true,
            participants: Number(d.n) || 0,
            mics: Number(d.m) || 0
        };
        ui.renderVoiceCard();

        if (Array.isArray(d.j) && d.j.length) {
            const names = d.j.slice(0, 3).map(p => p.n || `ID ${p.u}`).join(', ');
            ui.pushFeedItem({ icon: '🎙️', key: 'feed_voice_joined', vars: { names } });
        }
        if (Number(d.lt) > 0) {
            ui.pushFeedItem({ icon: '🚪', key: 'feed_voice_left', vars: { n: d.lt } });
        }
    },

    onVoiceCall(started, d) {
        state.liveVoice = started
            ? { active: true, callId: d?.call ?? null, participants: 0, mics: 0 }
            : { active: false, callId: null, participants: 0, mics: 0 };
        ui.renderVoiceCard();
        ui.pushFeedItem({
            icon: started ? '🎙️' : '🔇',
            key: started ? 'toast_voice_started' : 'toast_voice_ended',
            vars: {}
        });
        ui.notifyVoiceCall(started);
        tgApp.hapticImpact('light');
    },

    onStarsPayment(d) {
        if (!d) return;
        const stars = Number(d.a) || 0;
        const name = `ID ${d.u}`;   // el evento solo lleva el id del pagador

        const summary = state.liveAnalytics?.summary;
        if (summary) {
            summary.stars_total = (summary.stars_total || 0) + stars;
            summary.stars_today = (summary.stars_today || 0) + stars;
            summary.stars_30d = (summary.stars_30d || 0) + stars;
            summary.payments_count = (summary.payments_count || 0) + 1;
            ui.renderStarsKpis(summary);
            ui.flash('an-kpi-stars-total');
        }

        ui.pushFeedItem({ icon: '⭐', key: 'feed_payment', vars: { name, stars } });
        ui.notifyStarsPayment({ name, stars });
        tgApp.hapticNotification('success');
        this.scheduleAnalyticsRefresh(2000);
    },

    // --- Preferencias y ciclo de vida de la pestaña ------------------------
    initLiveToastPreference() {
        let saved = null;
        try { saved = localStorage.getItem('bunker_live_toasts'); } catch (e) { /* almacenamiento bloqueado */ }
        state.liveToastsEnabled = saved !== '0';
        ui.renderToastToggle();
    },

    toggleLiveToasts() {
        state.liveToastsEnabled = !state.liveToastsEnabled;
        try { localStorage.setItem('bunker_live_toasts', state.liveToastsEnabled ? '1' : '0'); } catch (e) { /* almacenamiento bloqueado */ }
        ui.renderToastToggle();
        if (!state.liveToastsEnabled) ui.clearToasts();
        ui.showToast({
            key: 'toast-pref',
            force: true,
            icon: state.liveToastsEnabled ? '🔔' : '🔕',
            ttl: 2000,
            title: ui.t(state.liveToastsEnabled ? 'an_toasts_on' : 'an_toasts_off')
        });
        tgApp.hapticSelection();
    },

    /** En segundo plano > 60 s se pausa el socket (ahorra batería y datos); al volver se reanuda y se reconcilia. */
    initVisibilityHandling() {
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                clearTimeout(this._hiddenTimer);
                this._hiddenTimer = setTimeout(() => state.wsClient?.pause(), CONFIG.WS?.HIDDEN_PAUSE_MS || 60000);
                return;
            }
            clearTimeout(this._hiddenTimer);
            this._hiddenTimer = null;
            const client = state.wsClient;
            if (client && client.status === 'paused') {
                this._silentUntil = Date.now() + 15000;   // la reconexión al volver del segundo plano es silenciosa
                client.resume();
            } else if (client && typeof client.probe === 'function') {
                // < 60 s en segundo plano: el socket no se pausó, pero los timers estuvieron congelados y la red
                // pudo cambiar (Wi-Fi ↔ datos). Ping inmediato: si no hay pong en PONG_TIMEOUT_MS se reconecta.
                client.probe();
            }
        });
    },

    async loadChatDashboard(chatId) {
        const epoch = session.epoch;
        const chatEpoch = state.chatEpoch;
        const data = await api.fetchChatDashboard(chatId);
        if (!data || data.__error || data.__stale || epoch !== session.epoch) return;
        // El operador eligió otra comunidad mientras se cargaba (configureChat avanza chatEpoch):
        // esta respuesta ya no le corresponde y no debe pisar los interruptores de la nueva.
        if (chatEpoch !== state.chatEpoch) return;

        state.data.currentChatDashboard = data;
        if (data.title && state.wsClient && String(state.wsClient.chatId) === String(chatId)) {
            ui.setRadarTarget(data.title);
        }

        // 1. Canal de Registro (Log Channel)
        ui.renderLogChannelStatus(data.log_channel);

        // 2. Estado del Plan Tarifario
        const planStatusEl = document.getElementById('chat-plan-status');
        if (planStatusEl) {
            const isActive = data.plan?.status === 'active';
            planStatusEl.innerText = isActive
                ? `${ui.t('tariff_active')} (${data.plan.name || ''})`
                : ui.t('tariff_empty');
            planStatusEl.className = isActive ? 'text-xs font-bold text-emerald-400 mt-1' : 'text-xs font-bold text-rose-400 mt-1';
        }

        // 3. Los interruptores los pinta la consola de programación (GET /configuration), que es la fuente
        //    de verdad; el resumen del dashboard solo se usa si la consola aún no cargó esta comunidad.
        if (data.switches && typeof data.switches === 'object' && this._chatConfig.chatId !== String(chatId)) {
            const keys = CONFIG.CONSOLE?.SWITCH_KEYS || ['captcha', 'autolower', 'shield', 'linklock'];
            state.securitySwitches = Object.fromEntries(keys.map(k => [k, Boolean(data.switches[k])]));
            state.switchesChatId = String(chatId);
        }

        // 4. Modo de Spam
        const spamModeEl = document.getElementById('chat-spam-mode');
        if (spamModeEl && data.protection?.spam_mode) {
            spamModeEl.value = data.protection.spam_mode;
        }
    },

    // ======================================================================
    // 🧾 CANAL DE REGISTRO — modal nativo (window.prompt se descarta en iOS/Android WebView)
    // ======================================================================
    openLogChannelModal() {
        const chatId = this.selectedGroupId();
        if (!chatId) return this.consolePickFirst();
        const group = state.data.groups.find(g => String(g.id) === String(chatId));
        const dashboard = state.data.currentChatDashboard;
        const sameChat = dashboard && String(dashboard.chat_id ?? chatId) === String(chatId);
        const current = sameChat && dashboard.log_channel?.enabled ? String(dashboard.log_channel.channel_id || '') : '';
        this._logChannel = { chatId: String(chatId), epoch: session.epoch, busy: false };
        ui.openLogChannelView({ community: group ? group.title : String(chatId), current });
        tgApp.hapticImpact('light');
    },

    closeLogChannelModal() {
        this._logChannel = null;
        ui.closeLogChannelView();
    },

    async saveLogChannel() {
        const ctx = this._logChannel;
        if (!ctx || ctx.busy) return false;
        if (ctx.epoch !== session.epoch) {
            this.closeLogChannelModal();
            return false;
        }
        const input = document.getElementById('log-channel-input');
        const value = String(input?.value || '').trim();
        if (value && !(STUDIO_ALIAS_RE.test(value) || CHAT_ID_RE.test(value))) {
            ui.setFieldError('log-channel-input', ui.t('logm_err_format'));
            tgApp.hapticNotification('error');
            return false;
        }
        ui.setFieldError('log-channel-input', '');

        ctx.busy = true;
        ui.setLogChannelBusy(true);
        const res = await api.updateChatSettings(ctx.chatId, { log_channel_id: value });
        ctx.busy = false;
        if (res?.__stale || ctx.epoch !== session.epoch || this._logChannel !== ctx) return false;
        ui.setLogChannelBusy(false);

        if (res?.__error) {
            const message = String(res.__error).replace(/^log_channel_id:\s*/, '');
            if (res.__status === 422 || res.__status === 403) {
                ui.setFieldError('log-channel-input', message);
            } else {
                this.consoleToast('error', '⚠️', ui.t('logm_toast_error'), message);
            }
            tgApp.hapticNotification('error');
            return false;
        }

        const savedId = res?.log_channel_id !== undefined ? String(res.log_channel_id) : value;
        tgApp.hapticNotification('success');
        this.consoleToast('success', savedId ? '🧾' : '🚫', ui.t(savedId ? 'logm_toast_saved' : 'logm_toast_disabled'), savedId);
        ui.renderLogChannelStatus({ enabled: Boolean(savedId), channel_id: savedId });
        this.closeLogChannelModal();
        if (String(state.selectedChatId) === ctx.chatId) this.loadChatDashboard(ctx.chatId);
        return true;
    },

    async loadChatStats(chatId) {
        const data = await api.fetchChatStats(chatId);
        if (data && !data.__error) {
            state.data.currentChatStats = data;
        }
    },

    async loadChatAdminStats(chatId) {
        const data = await api.fetchChatAdminStats(chatId);
        const listEl = document.getElementById('chat-admin-stats-list');
        if (!listEl || data?.__stale) return;

        if (data && !data.__error && data.admins && data.admins.length > 0) {
            state.data.currentChatAdmins = data.admins;
            listEl.innerHTML = data.admins.map(adm => `
                <div class="bg-black/40 p-2.5 rounded-xl border border-neutral-800 flex items-center justify-between font-mono">
                    <div>
                        <p class="text-white font-bold">${ui.escapeHtml(adm.name)}</p>
                        <span class="text-[9px] text-neutral-400">${ui.escapeHtml(adm.role)}</span>
                    </div>
                    <div class="text-right">
                        <p class="text-[#00f3ff] font-bold">${ui.escapeHtml(adm.messages)} msgs</p>
                        <span class="text-[9px] text-neutral-500">${ui.escapeHtml(adm.replies)} replies</span>
                    </div>
                </div>
            `).join('');
        } else {
            listEl.innerHTML = `<div class="glass-panel p-4 text-center text-xs text-neutral-500 font-mono">${state.currentLang === 'es' ? 'Sin estadísticas de administradores' : 'No administrator statistics'}</div>`;
        }
    },

    async loadChatTopUsers(chatId) {
        const data = await api.fetchChatTopUsers(chatId);
        const listEl = document.getElementById('chat-top-users-list');
        if (!listEl || data?.__stale) return;

        if (data && !data.__error && data.top_users && data.top_users.length > 0) {
            state.data.currentChatTopUsers = data.top_users;
            const medals = ['🥇', '🥈', '🥉'];
            listEl.innerHTML = data.top_users.map(u => `
                <div class="bg-black/40 p-2.5 rounded-xl border border-neutral-800 flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <span class="font-bold text-xs">${medals[u.rank - 1] || `#${u.rank}`}</span>
                        <div>
                            <p class="text-white font-bold text-xs">${ui.escapeHtml(u.name)}</p>
                            <span class="text-[9px] text-neutral-400 font-mono">${ui.escapeHtml(u.badge || '')}</span>
                        </div>
                    </div>
                    <span class="text-[#00f3ff] font-bold text-xs font-mono">${ui.escapeHtml(u.messages)} msgs</span>
                </div>
            `).join('');
        } else {
            listEl.innerHTML = `<div class="glass-panel p-4 text-center text-xs text-neutral-500 font-mono">${state.currentLang === 'es' ? 'Sin registros de actividad reciente' : 'No recent activity records'}</div>`;
        }
    },

    /** 🧬 Despliegue de Bot Clon → api.updateChatSettings(chatId, { action: 'deploy_clone', bot_token }) */
    async deployClone() {
        const chatId = this.selectedGroupId();
        if (!chatId) return this.consolePickFirst();
        const tokenInput = document.getElementById('clone-bot-token');
        const token = String(tokenInput?.value || '').trim();
        if (!token) {
            tgApp.hapticNotification('warning');
            this.consoleToast('warn', '🔑', ui.t('gc_clone_err_token'));
            return false;
        }
        if (!CLONE_TOKEN_RE.test(token)) {
            tgApp.hapticNotification('error');
            this.consoleToast('error', '🔑', ui.t('gc_clone_err_format'));
            return false;
        }
        tgApp.hapticImpact('medium');
        const res = await this.runConsoleAction('btn-deploy-clone', chatId, id => api.updateChatSettings(id, { action: 'deploy_clone', bot_token: token }));
        if (!res) return false;
        if (res.__error || res.status !== 'success') {
            tgApp.hapticNotification('error');
            this.consoleToast('error', '❌', ui.tf('gc_clone_toast_error', { error: this.consoleErrorMessage(res) }));
            return false;
        }
        if (tokenInput) tokenInput.value = '';
        tgApp.hapticNotification('success');
        this.consoleToast('success', '🚀', ui.tf('gc_clone_toast_ok', { name: res.clone_username || 'Bot' }));
        return true;
    },

    /** 📡 Centinela MTProto → api.updateChatSettings(chatId, { action: 'connect_sentinel', session_string }) */
    async connectSentinel() {
        const chatId = this.selectedGroupId();
        if (!chatId) return this.consolePickFirst();
        const sessionInput = document.getElementById('sentinel-session-string');
        const sessionString = String(sessionInput?.value || '').trim();

        // El REST solo admite una String Session; el acceso por teléfono (código + 2FA) se hace en el bot.
        if (sessionString && /^\+?[\d\s()\-]{7,16}$/.test(sessionString)) {
            tgApp.hapticNotification('warning');
            this.consoleToast('warn', '📱', ui.t('sentinel_phone_hint'));
            return false;
        }
        if (!sessionString) {
            tgApp.hapticNotification('warning');
            this.consoleToast('warn', '🔑', ui.t('gc_sentinel_err_session'));
            return false;
        }
        tgApp.hapticImpact('medium');
        const res = await this.runConsoleAction('btn-connect-sentinel', chatId, id => api.updateChatSettings(id, { action: 'connect_sentinel', session_string: sessionString }));
        if (!res) return false;
        if (res.__error || res.status !== 'success') {
            tgApp.hapticNotification('error');
            this.consoleToast('error', '❌', ui.tf('gc_sentinel_toast_error', { error: this.consoleErrorMessage(res) }));
            return false;
        }
        if (sessionInput) sessionInput.value = '';
        tgApp.hapticNotification('success');
        this.consoleToast('success', '📡', ui.t('gc_sentinel_toast_ok'));
        return true;
    },

    /** 💀 Ghost Purge → api.updateChatSettings(chatId, { action: 'run_ghost_purge' }) */
    async runGhostPurge() {
        const chatId = this.selectedGroupId();
        if (!chatId) return this.consolePickFirst();
        tgApp.hapticNotification('warning');
        if (!await this.confirmAction(ui.t('gc_purge_confirm'))) return false;
        tgApp.hapticImpact('heavy');
        const res = await this.runConsoleAction('btn-ghost-purge', chatId, id => api.updateChatSettings(id, { action: 'run_ghost_purge' }));
        if (!res) return false;
        if (res.__error || res.status !== 'success') {
            tgApp.hapticNotification('error');
            this.consoleToast('error', '❌', ui.tf('gc_purge_toast_error', { error: this.consoleErrorMessage(res) }));
            return false;
        }
        tgApp.hapticNotification('success');
        this.consoleToast('success', '⚡', ui.t('gc_purge_toast_ok'));
        return true;
    },

    /**
     * Checkout exclusivo con Telegram Stars (XTR): abre el bot con el deep link de la suscripción y la
     * factura se paga dentro de Telegram. Un segundo argumento heredado (HTML en caché) se ignora: la
     * plataforma no cobra por ningún otro medio.
     */
    pagarPlan(plan) {
        if (!CONFIG.PRICES[plan]) return;
        state.selectedPlan = plan;

        const targetChat = state.selectedChatId ? `_${state.selectedChatId}` : '';
        const param = plan === 'pro' ? `sub_pro${targetChat}` : `sub_ultra${targetChat}`;
        tgApp.openTelegramLink(`https://t.me/${CONFIG.BOT_USERNAME}?start=${param}`);
        setTimeout(() => tgApp.closeApp(), 300);
    },

    copyText(text) {
        navigator.clipboard.writeText(text).then(() => {
            alert(state.currentLang === 'es' ? "¡Copiado al portapapeles! 📋" : "Copied to clipboard! 📋");
        });
    }
};

// Enlace al ámbito global para compatibilidad con el HTML
window.app = app;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => app.init());
} else {
    app.init();
}