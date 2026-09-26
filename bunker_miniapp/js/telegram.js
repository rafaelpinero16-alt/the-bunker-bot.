/* ==========================================================================
   THE BUNKER — COMMAND OS
   telegram.js — SDK de Telegram WebApp, Hápticos y Autenticación Dual
   ========================================================================== */

import { state } from './state.js';
import { CONFIG } from './config.js';

export const tgApp = {
    get tg() {
        return window.Telegram?.WebApp || null;
    },

    initViewport() {
        if (this.tg) {
            this.tg.expand();
            this.tg.ready();
        }
    },

    setThemeColors(theme) {
        if (!this.tg) return;
        const color = theme === 'light' ? '#f1f5f9' : '#030407';
        if (this.tg.setHeaderColor) {
            try { this.tg.setHeaderColor(color); } catch (e) {}
        }
        if (this.tg.setBackgroundColor) {
            try { this.tg.setBackgroundColor(color); } catch (e) {}
        }
    },

    hapticImpact(style = 'light') {
        if (this.tg?.HapticFeedback) {
            this.tg.HapticFeedback.impactOccurred(style);
        }
    },

    hapticNotification(type = 'success') {
        if (this.tg?.HapticFeedback) {
            this.tg.HapticFeedback.notificationOccurred(type);
        }
    },

    hapticSelection() {
        if (this.tg?.HapticFeedback) {
            this.tg.HapticFeedback.selectionChanged();
        }
    },

    openTelegramLink(url) {
        if (this.tg) {
            this.tg.openTelegramLink(url);
        } else {
            window.open(url, '_blank');
        }
    },

    closeApp() {
        if (this.tg) {
            this.tg.close();
        }
    },

    getAuthHeaders() {
        const headers = {};
        const tgInit = this.tg?.initData;
        if (tgInit && tgInit.trim() !== '') {
            localStorage.setItem('bunker_init_data', tgInit);
            headers['X-Telegram-Init-Data'] = tgInit;
            return headers;
        }

        const cachedInit = localStorage.getItem('bunker_init_data');
        if (cachedInit) {
            headers['X-Telegram-Init-Data'] = cachedInit;
        }

        const sessionToken = localStorage.getItem('bunker_session_token');
        if (sessionToken) {
            headers['Authorization'] = `Bearer ${sessionToken}`;
        }

        return headers;
    },

    renderTelegramWidget(onAuthCallbackName = 'app.handleTelegramWidgetAuth') {
        const container = document.getElementById('telegram-login-widget-container');
        if (!container || container.dataset.rendered === '1') return;
        container.innerHTML = '';

        const script = document.createElement('script');
        script.src = 'https://telegram.org/js/telegram-widget.js?22';
        script.setAttribute('data-telegram-login', CONFIG.BOT_USERNAME);
        script.setAttribute('data-size', 'large');
        script.setAttribute('data-radius', '12');
        script.setAttribute('data-onauth', `${onAuthCallbackName}(user)`);
        script.setAttribute('data-request-access', 'write');

        container.appendChild(script);
        container.dataset.rendered = '1';
    }
};