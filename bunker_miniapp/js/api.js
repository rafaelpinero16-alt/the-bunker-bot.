/* ==========================================================================
   THE BUNKER — COMMAND OS
   api.js — Cliente HTTP Centralizado y Comunicación con Railway API
   ========================================================================== */

import { CONFIG } from './config.js';
import { tgApp } from './telegram.js';
import { state } from './state.js';

function buildUrl(path) {
    const base = (CONFIG.API_BASE || '').replace(/\/+$/, '');
    const cleanPath = path.startsWith('/') ? path : `/${path}`;
    
    // Si la base no incluye ya /api y el path tampoco, lo prefijamos
    if (!base.endsWith('/api') && !cleanPath.startsWith('/api')) {
        return `${base}/api${cleanPath}`;
    }
    return `${base}${cleanPath}`;
}

export const api = {
    async get(path) {
        try {
            const url = buildUrl(path);
            const res = await fetch(url, {
                headers: tgApp.getAuthHeaders()
            });

            if (res.status === 401) {
                this.handleSessionExpired();
                return { __error: 'unauthorized' };
            }

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                return { __error: errData.detail || `HTTP ${res.status}` };
            }

            return await res.json();
        } catch (err) {
            console.error(`[API GET ERROR] ${path}:`, err);
            return { __error: 'network' };
        }
    },

    async post(path, body = {}) {
        try {
            const url = buildUrl(path);
            const res = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...tgApp.getAuthHeaders()
                },
                body: JSON.stringify(body)
            });

            if (res.status === 401) {
                this.handleSessionExpired();
                return { __error: 'unauthorized' };
            }

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                return { __error: errData.detail || `HTTP ${res.status}` };
            }

            return await res.json();
        } catch (err) {
            console.error(`[API POST ERROR] ${path}:`, err);
            return { __error: 'network' };
        }
    },

    handleSessionExpired() {
        if (window.Telegram?.WebApp?.initData) return;
        localStorage.removeItem('bunker_session_token');
        state.isAuthenticated = false;
        if (window.app?.showLoginGate) {
            window.app.showLoginGate('login_expired');
        }
    },

    // --- Endpoints de Telemetría y Ecosistema ---

    async fetchStats(context = 'global') {
        return await this.get(`/stats?context=${context}`);
    },

    async fetchChannels() {
        return await this.get('/channels');
    },

    async fetchGroups() {
        return await this.get('/groups');
    },

    async fetchSubscribers() {
        return await this.get('/subscribers');
    },

    async syncChats() {
        return await this.post('/sync-chats', {});
    },

    // --- Endpoints del Dashboard de Comunidad ---

    async fetchChatDashboard(chatId) {
        return await this.get(`/chat/${chatId}/dashboard`);
    },

    async fetchChatStats(chatId) {
        return await this.get(`/chat/${chatId}/stats`);
    },

    async fetchChatAdminStats(chatId) {
        return await this.get(`/chat/${chatId}/admin-stats`);
    },

    async fetchChatTopUsers(chatId) {
        return await this.get(`/chat/${chatId}/top-users`);
    },

    async updateChatSettings(chatId, settings) {
        return await this.post(`/chat/${chatId}/settings`, settings);
    },

    // --- Acciones Tácticas Ultra Pro ---

    async deployBotClone(chatId, botToken) {
        return await this.updateChatSettings(chatId, {
            action: 'deploy_clone',
            bot_token: botToken
        });
    },

    async connectSentinel(chatId, sessionString) {
        return await this.updateChatSettings(chatId, {
            action: 'connect_sentinel',
            session_string: sessionString
        });
    },

    async triggerGhostPurge(chatId) {
        return await this.updateChatSettings(chatId, {
            action: 'run_ghost_purge'
        });
    },

    // --- Sesión Web y Autenticación Widget ---

    async exchangeWebToken(tempToken) {
        return await this.post('/auth/exchange-token', { token: tempToken });
    },

    async verifyWebSession(sessionToken) {
        try {
            const url = buildUrl('/auth/session-check');
            const res = await fetch(url, {
                headers: { 'Authorization': `Bearer ${sessionToken}` }
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (err) {
            return { __error: 'invalid_session' };
        }
    },

    async authenticateWidget(userPayload) {
        return await this.post('/auth/telegram-widget', userPayload);
    }
};