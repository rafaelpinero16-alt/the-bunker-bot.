/* ==========================================================================
   THE BUNKER — COMMAND OS
   app.js — Orquestador Central Modular, Enrutador de Eventos y Ciclo de Vida
   Fase 5/6/7: Analítica en vivo, checkout Stars, Estudio de Canales reactivo y sesión estricta.
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

import { CONFIG, translations } from './config.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';
import { api, session, BunkerWebSocketClient } from './api.js';
import { ui } from './ui.js';

const STUDIO_ALIAS_RE = /^@[A-Za-z][A-Za-z0-9_]{4,31}$/;
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
 * Valida y normaliza el formulario del Estudio de Canales (función pura, sin DOM).
 * Devuelve { valid, values, errors }: `values` listo para enviar a updateChatSettings y `errors`
 * como { campo: { key, vars } } con claves i18n. El enlace VIP acaba siendo un botón inline de
 * Telegram: se rechaza cualquier esquema que no sea http(s), t.me o @alias (un javascript: o un
 * enlace malformado haría fallar la entrega al suscriptor).
 */
export function validateStudioForm(raw) {
    const cfg = CONFIG.STUDIO;
    const errors = {};
    const values = {};

    const link = String(raw?.target_link ?? '').trim();
    if (link.length > cfg.LINK_MAX) {
        errors.target_link = { key: 'studio_err_link_long', vars: { max: cfg.LINK_MAX } };
    } else if (link === '') {
        values.target_link = '';
    } else if (STUDIO_ALIAS_RE.test(link)) {
        values.target_link = `https://t.me/${link.slice(1)}`;
    } else if (STUDIO_TME_RE.test(link)) {
        values.target_link = `https://${link}`;
    } else if (isHttpUrl(link)) {
        values.target_link = link;
    } else {
        errors.target_link = { key: 'studio_err_link' };
    }

    const wholeNumber = (text, min, max) => {
        const clean = String(text ?? '').trim();
        if (!/^\d+$/.test(clean)) return null;
        const n = parseInt(clean, 10);
        return (n >= min && n <= max) ? n : null;
    };

    const price = wholeNumber(raw?.stars_price, cfg.PRICE_MIN, cfg.PRICE_MAX);
    if (price === null) errors.stars_price = { key: 'studio_err_price', vars: { min: cfg.PRICE_MIN, max: cfg.PRICE_MAX } };
    else values.stars_price = price;

    const days = wholeNumber(raw?.duration_days, cfg.DAYS_MIN, cfg.DAYS_MAX);
    if (days === null) errors.duration_days = { key: 'studio_err_days', vars: { min: cfg.DAYS_MIN, max: cfg.DAYS_MAX } };
    else values.duration_days = days;

    return { valid: Object.keys(errors).length === 0, values, errors };
}

export const app = {
    // Estado interno del Estudio de Canales (guardado reactivo)
    _studio: { channelId: null, timer: null, saving: false, dirty: false, last: null },
    validateStudioForm,

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

    async toggleSecuritySwitch(key) {
        const selectedGroup = document.getElementById('group-owner-select')?.value || state.selectedChatId;
        if (!selectedGroup) {
            alert(state.currentLang === 'es' ? '⚠️ Selecciona primero una comunidad administrada.' : '⚠️ Select a managed community first.');
            return;
        }

        const previousVal = Boolean(state.securitySwitches[key]);
        const newVal = !previousVal;
        state.securitySwitches[key] = newVal;

        const el = document.getElementById(`switch-${key}`);
        if (el) {
            el.className = newVal ? "text-emerald-400 font-bold" : "text-rose-400 font-bold";
            el.innerText = newVal 
                ? (state.currentLang === 'es' ? "ACTIVO 🟢" : "ACTIVE 🟢")
                : (state.currentLang === 'es' ? "BLOQUEADO 🔴" : "BLOCKED 🔴");
        }

        const payload = {};
        payload[key] = newVal;
        const res = await api.updateChatSettings(selectedGroup, payload);

        if (res?.__error) {
            state.securitySwitches[key] = previousVal;
            if (el) {
                el.className = previousVal ? "text-emerald-400 font-bold" : "text-rose-400 font-bold";
                el.innerText = previousVal 
                    ? (state.currentLang === 'es' ? "ACTIVO 🟢" : "ACTIVE 🟢")
                    : (state.currentLang === 'es' ? "BLOQUEADO 🔴" : "BLOCKED 🔴");
            }
            alert(state.currentLang === 'es' ? `⚠️ Error al guardar: ${res.__error}` : `⚠️ Save error: ${res.__error}`);
            return;
        }

        tgApp.hapticImpact('medium');
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

        state.chatEpoch += 1;
        state.selectedChatId = null;
        state.deepLinkTab = null;
        state.activeContext = 'global';
        state.securitySwitches = { captcha: true, autolower: true, shield: true, linklock: false };
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

    async syncChats() {
        if (state.isSyncing) return;
        state.isSyncing = true;

        const syncButtons = document.querySelectorAll('.sync-btn-trigger');
        syncButtons.forEach(btn => {
            btn.dataset.originalText = btn.innerText;
            btn.innerText = `⏳ ${ui.t('syncing')}`;
            btn.disabled = true;
        });

        tgApp.hapticImpact('medium');

        const res = await api.syncChats();
        if (res?.__stale) {
            state.isSyncing = false;
            return;
        }
        await Promise.all([
            this.loadStats(),
            this.loadChannels(),
            this.loadGroups(),
            this.loadSubscribers()
        ]);

        syncButtons.forEach(btn => {
            btn.innerText = btn.dataset.originalText || ui.t('sync');
            btn.disabled = false;
        });
        state.isSyncing = false;

        tgApp.hapticNotification('success');

        if (res && res.status === 'success') {
            alert(state.currentLang === 'es'
                ? `✅ Sincronización Exitosa con The Bunker Bot:\n\n• Canales detectados: ${res.total_channels}\n• Comunidades detectadas: ${res.total_groups}`
                : `✅ Synchronization Successful with The Bunker Bot:\n\n• Detected Channels: ${res.total_channels}\n• Detected Groups: ${res.total_groups}`);
        } else if (res?.__error) {
            alert(state.currentLang === 'es' ? `⚠️ Error de sincronización: ${res.__error}` : `⚠️ Synchronization error: ${res.__error}`);
        } else {
            alert(state.currentLang === 'es' ? '✅ Canales y grupos sincronizados.' : '✅ Channels and groups synced.');
        }
    },

    openAddBot(type) {
        const url = `https://t.me/${CONFIG.BOT_USERNAME}?start${type === 'channel' ? 'channel' : 'group'}=admin&admin=change_info+post_messages+edit_messages+delete_messages+restrict_members+invite_users+pin_messages+promote_members+manage_video_chats`;
        tgApp.openTelegramLink(url);
    },

    onChannelSelectChange(channelId) {
        const studio = this._studio;
        const idLabel = document.getElementById('channel-id-display') || document.getElementById('channel-id-text');
        if (idLabel) {
            idLabel.innerText = channelId ? `ID: ${channelId}` : 'ID: —';
        }

        const defaults = {
            target_link: '',
            stars_price: CONFIG.STUDIO.DEFAULT_PRICE,
            duration_days: CONFIG.STUDIO.DEFAULT_DAYS
        };

        if (channelId === studio.channelId) {
            // Misma selección (p. ej. recarga de listas): solo se refresca si no hay ediciones pendientes.
            if (studio.dirty) return;
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
            ui.fillStudioForm(defaults);
            return;
        }

        state.selectedChatId = channelId;

        // Consultar ajustes del canal para autorrellenar los campos (sin pisar lo que el operador esté escribiendo)
        const epoch = session.epoch;
        api.fetchChatDashboard(channelId).then(data => {
            if (!data || data.__error || data.__stale) return;
            if (epoch !== session.epoch || studio.channelId !== channelId || studio.dirty) return;
            const values = {
                target_link: data.target_link || '',
                stars_price: data.stars_price || defaults.stars_price,
                duration_days: data.duration_days || defaults.duration_days
            };
            ui.fillStudioForm(values);
            studio.last = values;
        });
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
        const map = { target_link: 'studio-target-link', stars_price: 'studio-stars-price', duration_days: 'studio-duration-days' };
        Object.entries(map).forEach(([field, id]) => {
            const err = result.errors[field];
            ui.setFieldError(id, err ? ui.tf(err.key, err.vars) : '');
        });
    },

    onStudioInput() {
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
            ui.setStudioStatus('error', res.__status === 403
                ? ui.t('studio_status_forbidden')
                : ui.tf('studio_status_error', { error: res.__error }));
            tgApp.hapticNotification('error');
            return false;
        }

        studio.last = payload;
        const current = validateStudioForm(ui.readStudioForm());
        const sameAsSent = current.valid
            && current.values.target_link === payload.target_link
            && current.values.stars_price === payload.stars_price
            && current.values.duration_days === payload.duration_days;

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
        return true;
    },

    /** Relee el dashboard del canal y confirma que el servidor devuelve lo que se acaba de guardar. */
    async verifyStudioSaved(channelId, payload, time) {
        const studio = this._studio;
        const epoch = session.epoch;
        const data = await api.fetchChatDashboard(channelId);
        if (!data || data.__error || data.__stale) return;   // sin lectura no hay veredicto: se queda "Guardado ✓"
        if (epoch !== session.epoch || studio.channelId !== channelId || studio.dirty) return;

        const trimSlash = (value) => String(value ?? '').trim().replace(/\/+$/, '');
        const server = {
            target_link: trimSlash(data.target_link),
            stars_price: Number(data.stars_price),
            duration_days: Number(data.duration_days)
        };
        const matches = server.target_link === trimSlash(payload.target_link)
            && server.stars_price === payload.stars_price
            && server.duration_days === payload.duration_days;

        if (matches) {
            ui.setStudioStatus('saved', ui.tf('studio_status_saved', { time }));
        } else {
            const values = `${server.stars_price} ⭐ · ${server.duration_days} ${ui.t('studio_days_unit')}`;
            ui.setStudioStatus('mismatch', ui.tf('studio_status_mismatch', { values }));
        }
    },

    /** Alias de compatibilidad: versiones anteriores del HTML llamaban a openChannelStudio() (Telegram cachea agresivamente). */
    async openChannelStudio() {
        return await this.saveChannelStudio({ auto: false });
    },

    async loadChannels() {
        const data = await api.fetchChannels();
        if (data?.__stale) return;
        if (data?.__error) {
            ui.renderChatListError('channels-list', ui.t('load_error'), 'loadChannels');
            return;
        }
        state.data.channels = (data && data.channels) || [];
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
        state.data.groups = (data && data.groups) || [];
        ui.renderChatList('groups-list', state.data.groups, ui.t('no_groups'));
        ui.populateSelect('group-owner-select', state.data.groups, ui.t('no_groups'));
        ui.populateSelect('analytics-chat-select', state.data.groups, ui.t('no_groups'), 'group');
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
        client.on('settings_updated', () => { if (isCurrent()) this.scheduleDashboardReload(chatId); });
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
            }
        });
    },

    async loadChatDashboard(chatId) {
        const data = await api.fetchChatDashboard(chatId);
        if (data && !data.__error) {
            state.data.currentChatDashboard = data;
            if (data.title && state.wsClient && String(state.wsClient.chatId) === String(chatId)) {
                ui.setRadarTarget(data.title);
            }

            // 1. Canal de Registro (Log Channel)
            const logEl = document.getElementById('chat-log-channel');
            if (logEl) {
                const isEnabled = Boolean(data.log_channel?.enabled);
                logEl.innerText = isEnabled 
                    ? `${ui.t('status_enabled')} (${ui.escapeHtml(data.log_channel.channel_id)})` 
                    : ui.t('status_disabled');
                logEl.className = isEnabled 
                    ? 'text-xs font-bold text-emerald-400 mt-1 truncate' 
                    : 'text-xs font-bold text-neutral-400 mt-1 truncate';
            }

            // 2. Estado del Plan Tarifario
            const planStatusEl = document.getElementById('chat-plan-status');
            if (planStatusEl) {
                const isActive = data.plan?.status === 'active';
                planStatusEl.innerText = isActive 
                    ? `${ui.t('tariff_active')} (${ui.escapeHtml(data.plan.name)})` 
                    : ui.t('tariff_empty');
                planStatusEl.className = isActive ? 'text-xs font-bold text-emerald-400 mt-1' : 'text-xs font-bold text-rose-400 mt-1';
            }

            // 3. Sincronizar interruptores de seguridad reales de la base de datos
            if (data.switches) {
                state.securitySwitches = { ...state.securitySwitches, ...data.switches };
                ['captcha', 'autolower', 'shield', 'linklock'].forEach(k => {
                    const el = document.getElementById(`switch-${k}`);
                    if (el) {
                        const active = Boolean(state.securitySwitches[k]);
                        el.className = active ? "text-emerald-400 font-bold" : "text-rose-400 font-bold";
                        el.innerText = active 
                            ? (state.currentLang === 'es' ? "ACTIVO 🟢" : "ACTIVE 🟢")
                            : (state.currentLang === 'es' ? "BLOQUEADO 🔴" : "BLOCKED 🔴");
                    }
                });
            }

            // 4. Modo de Spam
            const spamModeEl = document.getElementById('chat-spam-mode');
            if (spamModeEl && data.protection?.spam_mode) {
                spamModeEl.value = data.protection.spam_mode;
            }
        }
    },

    openLogChannelModal() {
        const selectedGroup = document.getElementById('group-owner-select')?.value || state.selectedChatId;
        if (!selectedGroup) {
            alert(state.currentLang === 'es' ? '⚠️ Selecciona primero una comunidad administrada.' : '⚠️ Select a managed community first.');
            return;
        }
        const currentLog = state.data.currentChatDashboard?.log_channel?.channel_id || '';
        const newLog = prompt(
            state.currentLang === 'es' 
                ? 'Ingresa el ID o @alias del canal de registro para auditorías:' 
                : 'Enter the ID or @alias of the log channel for audits:', 
            currentLog
        );
        if (newLog !== null) {
            api.updateChatSettings(selectedGroup, { log_channel_id: newLog.trim() }).then(() => {
                this.loadChatDashboard(selectedGroup);
                tgApp.hapticNotification('success');
            });
        }
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

    async deployClone() {
        const tokenInput = document.getElementById('clone-bot-token');
        const token = tokenInput?.value?.trim();
        const selectedGroup = document.getElementById('group-owner-select')?.value || state.selectedChatId;

        if (!token) {
            alert(state.currentLang === 'es' ? '⚠️ Ingresa el Bot Token generado en @BotFather.' : '⚠️ Enter the Bot Token from @BotFather.');
            return;
        }

        if (!selectedGroup) {
            alert(state.currentLang === 'es' ? '⚠️ Selecciona primero la comunidad administrada.' : '⚠️ Select the managed community first.');
            return;
        }

        const res = await api.deployBotClone(selectedGroup, token);

        if (res?.status === 'success') {
            tgApp.hapticNotification('success');
            alert(state.currentLang === 'es'
                ? `🚀 Bot Clon (@${res.clone_username || 'Bot'}) desplegado y activo en memoria exitosamente.`
                : `🚀 Bot Clone (@${res.clone_username || 'Bot'}) deployed and active in memory.`);
            if (tokenInput) tokenInput.value = '';
        } else {
            alert(state.currentLang === 'es'
                ? `❌ Error al desplegar clon: ${res?.__error || 'Token inválido o bot inaccesible.'}`
                : `❌ Failed to deploy clone: ${res?.__error || 'Invalid token.'}`);
        }
    },

    async connectSentinel() {
        const sessionInput = document.getElementById('sentinel-session-string');
        const sessionString = sessionInput?.value?.trim();
        const selectedGroup = document.getElementById('group-owner-select')?.value || state.selectedChatId;

        // El REST solo admite una String Session; el acceso por teléfono (código + 2FA) se hace en el bot.
        if (sessionString && /^\+?[\d\s()\-]{7,16}$/.test(sessionString)) {
            alert(ui.t('sentinel_phone_hint'));
            return;
        }

        if (!sessionString) {
            alert(state.currentLang === 'es' ? '⚠️ Pega la String Session generada para tu Centinela MTProto.' : '⚠️ Paste the String Session for your MTProto Sentinel.');
            return;
        }

        if (!selectedGroup) {
            alert(state.currentLang === 'es' ? '⚠️ Selecciona primero la comunidad administrada.' : '⚠️ Select the managed community first.');
            return;
        }

        const res = await api.connectSentinel(selectedGroup, sessionString);

        if (res?.status === 'success') {
            tgApp.hapticNotification('success');
            alert(state.currentLang === 'es'
                ? '📡 Centinela Acústico MTProto conectado al clúster de The Bunker exitosamente.'
                : '📡 MTProto Acoustic Sentinel connected to The Bunker cluster successfully.');
            if (sessionInput) sessionInput.value = '';
        } else {
            alert(state.currentLang === 'es'
                ? `❌ Error al conectar centinela: ${res?.__error || 'Error de sesión.'}`
                : `❌ Failed to connect sentinel: ${res?.__error || 'Session error.'}`);
        }
    },

    async runGhostPurge() {
        const selectedGroup = document.getElementById('group-owner-select')?.value || state.selectedChatId;

        if (!selectedGroup) {
            alert(state.currentLang === 'es' ? '⚠️ Selecciona primero la comunidad a purgar.' : '⚠️ Select the community to purge first.');
            return;
        }

        if (!confirm(state.currentLang === 'es' ? '💀 ¿Deseas iniciar la purga de cuentas fantasma y perfiles eliminados?' : '💀 Start purging ghost accounts and deleted profiles?')) {
            return;
        }

        const res = await api.triggerGhostPurge(selectedGroup);

        if (res?.status === 'success') {
            tgApp.hapticImpact('heavy');
            alert(state.currentLang === 'es'
                ? '⚡ Orden de Ghost Purge enviada al Búnker Bot. La purga se está ejecutando en segundo plano.'
                : '⚡ Ghost Purge command sent. Purge is running in background.');
        } else {
            alert(state.currentLang === 'es'
                ? `❌ Error al iniciar Ghost Purge: ${res?.__error || 'Fallo de comunicación.'}`
                : `❌ Error triggering Ghost Purge: ${res?.__error || 'Communication failure.'}`);
        }
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