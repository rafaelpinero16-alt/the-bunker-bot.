/* ==========================================================================
   THE BUNKER — COMMAND OS
   api.js — Cliente HTTP Centralizado y Comunicación con Railway API
   ========================================================================== */

import { CONFIG } from './config.js';
import { tgApp } from './telegram.js';
import { state } from './state.js';

export const api = {
    async get(path) {
        try {
            const res = await fetch(`${CONFIG.API_BASE}${path}`, {
                headers: tgApp.getAuthHeaders()
            });
            if (res.status === 401) {
                this.handleSessionExpired();
                return { __error: 'unauthorized' };
            }
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (err) {
            console.error(`[API GET ERROR] ${path}:`, err);
            return { __error: 'network' };
        }
    },

    async post(path, body = {}) {
        try {
            const res = await fetch(`${CONFIG.API_BASE}${path}`, {
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
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
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

    // --- Endpoints Específicos del Búnker ---

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

    async exchangeWebToken(tempToken) {
        return await this.post('/auth/exchange-token', { token: tempToken });
    },

    async verifyWebSession(sessionToken) {
        try {
            const res = await fetch(`${CONFIG.API_BASE}/auth/session-check`, {
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