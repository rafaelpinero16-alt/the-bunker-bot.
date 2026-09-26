/* ==========================================================================
   THE BUNKER — COMMAND OS
   app.js — Orquestador Central Modular, Enrutador de Eventos y Ciclo de Vida
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

import { CONFIG, translations } from './config.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';
import { api } from './api.js';
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
        this.initTonConnect();
        ui.updateTranslations();
        this.initDraggableButton();
        this.initCharCounter();
        this.initAuth();
    },

    initTheme() {
        const savedTheme = localStorage.getItem('bunker_theme') || 'dark';
        this.setTheme(savedTheme);
    },

    setTheme(theme) {
        ui.setTheme(theme);
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
        state.securitySwitches[key] = !state.securitySwitches[key];
        const el = document.getElementById(`switch-${key}`);
        const active = state.securitySwitches[key];

        if (el) {
            if (active) {
                el.className = "text-emerald-400 font-bold";
                el.innerText = state.currentLang === 'es' ? "ACTIVO 🟢" : "ACTIVE 🟢";
            } else {
                el.className = "text-rose-400 font-bold";
                el.innerText = state.currentLang === 'es' ? "BLOQUEADO 🔴" : "BLOCKED 🔴";
            }
        }

        const selectedGroup = document.getElementById('group-owner-select')?.value || state.selectedChatId;
        if (selectedGroup) {
            const payload = {};
            payload[key] = active;
            await api.updateChatSettings(selectedGroup, payload);
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

    bootstrapDashboard() {
        this.loadTelegramUser();
        this.loadAffiliateLink();
        this.loadStats();
        this.loadChannels();
        this.loadGroups();
        this.loadSubscribers();
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
            alert(state.currentLang === 'es' ? '⚠️ Error de sincronización con el servidor.' : '⚠️ Server synchronization error.');
        } else {
            alert(state.currentLang === 'es' ? '✅ Canales y grupos sincronizados.' : '✅ Channels and groups synced.');
        }
    },

    openAddBot(type) {
        const url = `https://t.me/${CONFIG.BOT_USERNAME}?start${type === 'channel' ? 'channel' : 'group'}=admin&admin=change_info+post_messages+edit_messages+delete_messages+restrict_members+invite_users+pin_messages+promote_members+manage_video_chats`;
        tgApp.openTelegramLink(url);
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

    async configureChat(chatId) {
        if (!chatId) return;
        state.selectedChatId = chatId;

        document.querySelectorAll('#group-owner-select').forEach(sel => {
            sel.value = chatId;
        });

        await Promise.all([
            this.loadChatDashboard(chatId),
            this.loadChatStats(chatId),
            this.loadChatAdminStats(chatId),
            this.loadChatTopUsers(chatId)
        ]);

        this.switchTab('bot-settings');
    },

    async loadChatDashboard(chatId) {
        const data = await api.fetchChatDashboard(chatId);
        if (data && !data.__error) {
            state.data.currentChatDashboard = data;
            const logEl = document.getElementById('chat-log-channel');
            if (logEl) {
                const isEnabled = Boolean(data.log_channel?.enabled);
                logEl.innerText = isEnabled 
                    ? `${ui.t('status_enabled')} (${ui.escapeHtml(data.log_channel.channel_id)})` 
                    : ui.t('status_disabled');
            }

            const planStatusEl = document.getElementById('chat-plan-status');
            if (planStatusEl) {
                const isActive = data.plan?.status === 'active';
                planStatusEl.innerText = isActive 
                    ? `${ui.t('tariff_active')} (${ui.escapeHtml(data.plan.name)})` 
                    : ui.t('tariff_empty');
                planStatusEl.className = isActive ? 'text-xs font-bold text-emerald-400 mt-1' : 'text-xs font-bold text-rose-400 mt-1';
            }

            const modsEl = document.getElementById('chat-active-modules');
            if (modsEl && data.modules) {
                modsEl.innerHTML = `${ui.escapeHtml(data.modules.active)} <span class="text-sm font-normal text-blue-300">/ ${ui.escapeHtml(data.modules.total)}</span>`;
            }

            const spamModeEl = document.getElementById('chat-spam-mode');
            if (spamModeEl && data.protection?.spam_mode) {
                spamModeEl.value = data.protection.spam_mode;
            }
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
        const targetLink = document.getElementById('studio-target-link')?.value || '';
        const price = parseInt(document.getElementById('studio-stars-price')?.value || '150');
        const days = parseInt(document.getElementById('studio-duration-days')?.value || '30');
        const channelSelect = document.getElementById('channel-owner-select');
        const selectedChannel = channelSelect?.value;

        if (selectedChannel) {
            await api.updateChatSettings(selectedChannel, {
                target_link: targetLink,
                stars_price: price,
                duration_days: days
            });
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

        await api.updateChatSettings(selectedGroup, {
            action: 'deploy_clone',
            bot_token: token
        });

        tgApp.hapticNotification('success');

        alert(state.currentLang === 'es'
            ? '🚀 Bot Clon sincronizado y puesto en marcha en memoria exitosamente.'
            : '🚀 Bot Clone synchronized and running in memory successfully.');

        if (tokenInput) tokenInput.value = '';
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

        await api.updateChatSettings(selectedGroup, {
            action: 'connect_sentinel',
            session_string: sessionString
        });

        tgApp.hapticNotification('success');

        alert(state.currentLang === 'es'
            ? '📡 Centinela Acústico MTProto conectado al clúster de The Bunker.'
            : '📡 MTProto Acoustic Sentinel connected to The Bunker cluster.');

        if (sessionInput) sessionInput.value = '';
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

        await api.updateChatSettings(selectedGroup, {
            action: 'run_ghost_purge'
        });

        tgApp.hapticImpact('heavy');

        alert(state.currentLang === 'es'
            ? '⚡ Orden de Ghost Purge enviada al Búnker Bot. La purga se ejecutará en segundo plano.'
            : '⚡ Ghost Purge command sent. Purge is running in background.');
    },

    async initTonConnect() {
        const TonConnectClass = window.TON_CONNECT_UI?.TonConnectUI || window.TonConnectUI;
        if (!state.tonConnectUI && TonConnectClass) {
            try {
                state.tonConnectUI = new TonConnectClass({
                    manifestUrl: CONFIG.TON_MANIFEST,
                    uiPreferences: { theme: 'DARK' }
                });

                await state.tonConnectUI.connectionRestored;
                ui.updateWalletUI(state.tonConnectUI.connected ? state.tonConnectUI.account : null);
                state.tonConnectUI.onStatusChange(async (wallet) => ui.updateWalletUI(wallet?.account));
            } catch (err) {
                console.warn('[TON Connect]', err);
            }
        }
    },

    async connectWallet() {
        try {
            if (!state.tonConnectUI) await this.initTonConnect();
            if (!state.tonConnectUI) return;

            if (state.tonConnectUI.connected) {
                if (confirm(state.currentLang === 'es' ? "¿Desconectar TON Wallet?" : "Disconnect TON Wallet?")) {
                    await state.tonConnectUI.disconnect();
                    ui.updateWalletUI(null);
                }
            } else {
                await state.tonConnectUI.openModal();
            }
        } catch (e) {
            console.error('Wallet error:', e);
        }
    },

    async executeTonPayment(plan) {
        const nanoAmount = CONFIG.PRICES[plan]?.ton || '1000000000';
        const tx = {
            validUntil: Math.floor(Date.now() / 1000) + 300,
            messages: [{ address: CONFIG.TON_WALLET, amount: nanoAmount }]
        };
        try {
            const result = await state.tonConnectUI.sendTransaction(tx);
            if (result) {
                alert(state.currentLang === 'es' ? "✅ Transacción exitosa. ¡Activación procesada!" : "✅ Transaction successful.");
                this.closeCheckout();
            }
        } catch (err) {
            console.warn('Tx cancelada:', err);
        }
    },

    pagarPlan(plan, method) {
        state.selectedPlan = plan;
        const priceInfo = CONFIG.PRICES[plan];
        if (!priceInfo) return;

        if (method === 'stars') {
            const param = plan === 'pro' ? 'sub_pro' : 'sub_ultra';
            tgApp.openTelegramLink(`https://t.me/${CONFIG.BOT_USERNAME}?start=${param}`);
            setTimeout(() => tgApp.closeApp(), 300);
        } else if (method === 'paypal') {
            window.open(`https://paypal.me/Felipecosmic/${priceInfo.usd}`, '_blank');
        } else if (method === 'ton') {
            if (!state.tonConnectUI || !state.tonConnectUI.connected) {
                alert(state.currentLang === 'es' ? "⚠️ Conecta tu TON Wallet primero." : "⚠️ Connect your TON Wallet first.");
                this.connectWallet();
                return;
            }
            this.executeTonPayment(plan);
        } else if (method === 'external') {
            const label = plan === 'pro' ? `PRO ($${priceInfo.usd}.00)` : `ULTRA PRO ($${priceInfo.usd}.00)`;
            const planEl = document.getElementById('checkout-plan-name');
            if (planEl) planEl.innerText = label;
            document.getElementById('modal-external-checkout')?.classList.remove('hidden');
        }
    },

    processOneClickPay(gateway) {
        const priceUsd = `${CONFIG.PRICES[state.selectedPlan]?.usd || 5}.00`;
        const links = {
            skrill: `https://skrill.me/rq/Felipe%20Rafael/${priceUsd}/USD?key=7AR7OlqodIdbV_WU4hSXJ435Na1`,
            binance: 'https://app.binance.com/uni-qr/request-to-pay?billOrderId=452405181270605824&billType=request_a_payment'
        };
        if (links[gateway]) {
            window.open(links[gateway], '_blank', 'noopener,noreferrer');
            this.closeCheckout();
        }
    },

    openManualPayment() {
        document.getElementById('modal-external-checkout')?.classList.add('hidden');
        document.getElementById('modal-manual-payment')?.classList.remove('hidden');
    },

    closeCheckout() {
        document.getElementById('modal-external-checkout')?.classList.add('hidden');
        document.getElementById('modal-manual-payment')?.classList.add('hidden');
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