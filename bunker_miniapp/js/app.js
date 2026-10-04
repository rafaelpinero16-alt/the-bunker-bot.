/* ==========================================================================
   THE BUNKER — COMMAND OS
   app.js — Orquestador Central Modular, Enrutador de Eventos y Ciclo de Vida
   Fase 5/6: Analítica en vivo (REST + WebSocket /ws/radar) y checkout con Telegram Stars.
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

import { CONFIG, translations } from './config.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';
import { api, BunkerWebSocketClient } from './api.js';
import { ui } from './ui.js';

export const app = {
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
        this.initTheme();
        ui.ensureLiveStyles();
        ui.updateTranslations();
        ui.setWsStatus('idle');
        this.initLiveToastPreference();
        this.initVisibilityHandling();
        this.initDraggableButton();
        this.initCharCounter();
        this.bindChannelSelectListener();
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

    initUrlRouting() {
        const urlParams = new URLSearchParams(window.location.search);
        const isNumericId = (value) => /^-?\d+$/.test(String(value || ''));

        // Comunidad por ?chat_id= (botones de /top, /heatmap y /metrics) o por startapp=<chat_id>
        // (Direct Link de la Mini App en grupos, donde Telegram no admite botones web_app).
        const startParam = tgApp.tg?.initDataUnsafe?.start_param || '';
        const rawChat = urlParams.get('chat_id') || (isNumericId(startParam) ? startParam : '');
        const channelId = urlParams.get('channel_id');

        if (isNumericId(rawChat)) {
            state.selectedChatId = String(rawChat);
            state.activeContext = 'groups';
            state.deepLinkTab = 'analytics';   // el botón "Abrir Dashboard en Vivo" debe aterrizar en la analítica
        } else if (channelId) {
            state.selectedChatId = channelId;
            state.activeContext = 'channels';
            const select = document.getElementById('channel-owner-select');
            if (select) {
                select.value = channelId;
            }
            this.onChannelSelectChange(channelId);
        }
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
    openCompanyRegModal() { document.getElementById('modal-company-reg')?.classList.remove('hidden'); },
    closeCompanyRegModal() { document.getElementById('modal-company-reg')?.classList.add('hidden'); },

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

    startCompanyReg() {
        alert(state.currentLang === 'es' ? 'Iniciando proceso corporativo...' : 'Starting corporate process...');
        this.closeCompanyRegModal();
    },

    loadTelegramUser() {
        const tgUser = tgApp.tg?.initDataUnsafe?.user;
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
        const user = tgApp.tg?.initDataUnsafe?.user;
        const userId = user ? user.id : (state.webUser?.id || '8269470905');
        ui.renderAffiliateLink(userId);
    },

    copyAffiliateLink() {
        const user = tgApp.tg?.initDataUnsafe?.user;
        const userId = user ? user.id : (state.webUser?.id || '8269470905');
        const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}`;
        this.copyText(link);
    },

    logout() {
        if (confirm(state.currentLang === 'es' ? "¿Deseas cerrar la sesión de The Bunker OS?" : "Close The Bunker OS session?")) {
            this.disconnectLiveRadar();
            localStorage.removeItem('bunker_session_token');
            localStorage.removeItem('bunker_init_data');
            if (tgApp.tg) {
                tgApp.closeApp();
            } else {
                state.isAuthenticated = false;
                state.webUser = null;
                this.toggleDrawer(false);
                this.showLoginGate();
            }
        }
    },

    initAuth() {
        const tgInitData = tgApp.tg?.initData;
        if (tgInitData && tgInitData.trim() !== '') {
            state.isAuthenticated = true;
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

        const savedToken = localStorage.getItem('bunker_session_token');
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
            window.history.replaceState({}, document.title, window.location.pathname);
            state.webUser = data.user;
            state.isAuthenticated = true;
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
            this.showAppShell();
            this.bootstrapDashboard();
        } else {
            localStorage.removeItem('bunker_session_token');
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
        const idLabel = document.getElementById('channel-id-display') || document.getElementById('channel-id-text');
        if (idLabel) {
            idLabel.innerText = channelId ? `ID: ${channelId}` : 'ID: —';
        }

        if (!channelId) {
            const targetEl = document.getElementById('studio-target-link');
            const priceEl = document.getElementById('studio-stars-price');
            const daysEl = document.getElementById('studio-duration-days');
            if (targetEl) targetEl.value = '';
            if (priceEl) priceEl.value = '150';
            if (daysEl) daysEl.value = '30';
            return;
        }

        state.selectedChatId = channelId;

        // Consultar ajustes del canal para autorrellenar los campos
        api.fetchChatDashboard(channelId).then(data => {
            if (data && !data.__error) {
                if (data.target_link !== undefined && document.getElementById('studio-target-link')) {
                    document.getElementById('studio-target-link').value = data.target_link || '';
                }
                if (data.stars_price !== undefined && document.getElementById('studio-stars-price')) {
                    document.getElementById('studio-stars-price').value = data.stars_price || 150;
                }
                if (data.duration_days !== undefined && document.getElementById('studio-duration-days')) {
                    document.getElementById('studio-duration-days').value = data.duration_days || 30;
                }
            }
        });
    },

    bindChannelSelectListener() {
        const select = document.getElementById('channel-owner-select');
        if (!select || select.dataset.bound) return;

        select.dataset.bound = 'true';
        select.addEventListener('change', (e) => {
            this.onChannelSelectChange(e.target.value);
            tgApp.hapticSelection();
        });
    },

    async loadChannels() {
        const data = await api.fetchChannels();
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
        this.disconnectLiveRadar();   // una sola conexión: se cierra la de la comunidad anterior

        const client = new BunkerWebSocketClient(chatId);
        state.wsClient = client;
        const isCurrent = () => state.wsClient === client;

        client.on('status', ({ status }) => { if (isCurrent()) ui.setWsStatus(status); });
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

            // 3. Módulos Activos
            const modsEl = document.getElementById('chat-active-modules');
            if (modsEl && data.modules) {
                modsEl.innerHTML = `${ui.escapeHtml(data.modules.active)} <span class="text-sm font-normal text-blue-300">/ ${ui.escapeHtml(data.modules.total)}</span>`;
            }

            // 4. Sincronizar interruptores de seguridad reales de la base de datos
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

            // 5. Modo de Spam
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

    openModulesManager() {
        const selectedGroup = document.getElementById('group-owner-select')?.value || state.selectedChatId;
        if (!selectedGroup) {
            alert(state.currentLang === 'es' ? '⚠️ Selecciona una comunidad para gestionar sus 91 módulos.' : '⚠️ Select a community to manage its 91 modules.');
            return;
        }
        this.switchTab('bot-settings');
        tgApp.hapticImpact('medium');
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
        if (!listEl) return;

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
        if (!listEl) return;

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

    async openChannelStudio() {
        const channelSelect = document.getElementById('channel-owner-select');
        const selectedChannel = channelSelect?.value;

        if (!selectedChannel) {
            alert(state.currentLang === 'es' ? '⚠️ Selecciona primero un canal bajo tu ID de creador.' : '⚠️ Select a channel under your Creator ID first.');
            return;
        }

        const targetLink = document.getElementById('studio-target-link')?.value || '';
        const price = parseInt(document.getElementById('studio-stars-price')?.value || '150');
        const days = parseInt(document.getElementById('studio-duration-days')?.value || '30');

        const res = await api.updateChatSettings(selectedChannel, {
            target_link: targetLink,
            stars_price: price,
            duration_days: days
        });

        if (res?.__error) {
            alert(state.currentLang === 'es' ? `⚠️ Error al guardar los ajustes: ${res.__error}` : `⚠️ Error saving settings: ${res.__error}`);
            return;
        }

        tgApp.hapticNotification('success');

        alert(state.currentLang === 'es'
            ? `📡 Sincronización del Estudio Exitosa:\n\n• Tarifa: ${price} Stars (XTR)\n• Duración: ${days} días\n• Destino VIP: ${targetLink || 'Guardado'}`
            : `📡 Studio Sync Successful:\n\n• Rate: ${price} Stars (XTR)\n• Duration: ${days} days\n• VIP Target: ${targetLink || 'Saved'}`);
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
     * factura se paga dentro de Telegram. `method` se conserva solo por compatibilidad con HTML en caché:
     * cualquier valor distinto de 'stars' se trata como Stars (la plataforma no cobra por otros medios).
     */
    pagarPlan(plan, method = 'stars') {
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