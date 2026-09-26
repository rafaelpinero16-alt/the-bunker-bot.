/* ==========================================================================
   THE BUNKER — COMMAND OS
   ui.js — Renderizado del DOM, Componentes Visuales, Sparklines y TON UI
   ========================================================================== */

import { CONFIG, translations } from './config.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';

export const ui = {
    t(key) {
        return (translations[state.currentLang] && translations[state.currentLang][key]) || key;
    },

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    updateTranslations() {
        const dict = translations[state.currentLang];
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (dict[key]) el.innerText = dict[key];
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            if (dict[key]) el.placeholder = dict[key];
        });
    },

    setTheme(theme) {
        state.currentTheme = theme;
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('bunker_theme', theme);

        const btnLight = document.getElementById('theme-btn-light');
        const btnDark = document.getElementById('theme-btn-dark');

        if (btnLight && btnDark) {
            if (theme === 'light') {
                btnLight.className = "flex-1 py-1.5 rounded-lg flex items-center justify-center gap-1.5 font-bold bg-[#00f3ff]/20 text-[#00a8b3] border border-[#00a8b3]/40 transition";
                btnDark.className = "flex-1 py-1.5 rounded-lg flex items-center justify-center gap-1.5 font-bold text-neutral-400 transition";
            } else {
                btnDark.className = "flex-1 py-1.5 rounded-lg flex items-center justify-center gap-1.5 font-bold bg-[#00f3ff]/20 text-[#00f3ff] border border-[#00f3ff]/40 transition";
                btnLight.className = "flex-1 py-1.5 rounded-lg flex items-center justify-center gap-1.5 font-bold text-neutral-400 transition";
            }
        }
        tgApp.setThemeColors(theme);
    },

    setStat(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerText = (value === null || value === undefined || value === '') ? '0' : value;
    },

    renderStats(data) {
        this.setStat('stat-subs-count', data?.subscribers ?? 0);
        this.setStat('stat-revenue-count', data?.revenue_stars != null ? `${data.revenue_stars} ⭐` : '0 ⭐');
        this.setStat('stat-verified', data?.verified ?? 0);
        this.setStat('stat-expelled', data?.expelled ?? 0);
        this.setStat('stat-purges', data?.purges ?? 0);

        const profileBal = document.getElementById('profile-balance-stars');
        if (profileBal) {
            profileBal.innerText = data?.revenue_stars != null ? `${data.revenue_stars} ⭐` : '0 ⭐';
        }
    },

    generateSparkline(data, color) {
        const width = 300, height = 75;
        if (!data || data.length < 2) return '';
        const c = color || '#00f3ff';
        const max = Math.max(...data), min = Math.min(...data);
        const range = (max - min) || 1;
        const stepX = width / (data.length - 1);
        const pts = data.map((v, i) => [i * stepX, height - ((v - min) / range) * (height - 12) - 6]);
        const line = pts.map((p, i) => (i === 0 ? `M${p[0].toFixed(1)},${p[1].toFixed(1)}` : `L${p[0].toFixed(1)},${p[1].toFixed(1)}`)).join(' ');
        const area = `${line} L${width},${height} L0,${height} Z`;
        const gid = `spark-${Math.random().toString(36).slice(2, 9)}`;
        return `<svg viewBox="0 0 ${width} ${height}" class="w-full h-full overflow-visible" preserveAspectRatio="none">
            <defs>
                <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="${c}" stop-opacity="0.4"/>
                    <stop offset="100%" stop-color="${c}" stop-opacity="0"/>
                </linearGradient>
            </defs>
            <path d="${area}" fill="url(#${gid})"/>
            <path d="${line}" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter:drop-shadow(0 0 4px ${c})"/>
        </svg>`;
    },

    chatCardTemplate(chat) {
        const isChannel = chat.type === 'channel';
        const licenseActive = chat.license_status === 'active';
        const safeId = this.escapeHtml(chat.id);
        const safeTitle = this.escapeHtml(chat.title);
        const safeMembers = this.escapeHtml(chat.members ?? '0');

        const statusHtml = licenseActive
            ? `<span class="text-emerald-400 font-bold text-[10px] shrink-0">${this.t('license_active')}</span>`
            : `<button onclick="app.renewLicense('${safeId}')" class="bg-rose-500/20 text-rose-400 border border-rose-500/50 px-2.5 py-1 rounded-lg text-[10px] font-bold hover:bg-rose-500/30 active:scale-95 transition shrink-0">${this.t('license_renew')}</button>`;

        const deltaHtml = (chat.joined != null)
            ? `<span class="text-emerald-400">+${this.escapeHtml(chat.joined)}</span> <span class="text-rose-400 ml-1.5">-${this.escapeHtml(chat.left)}</span>`
            : `<span class="text-neutral-500">—</span>`;

        return `
        <div class="glass-panel p-3.5 space-y-2.5" data-chat-id="${safeId}">
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                    <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-[#00f3ff]/25 to-[#ff00ff]/25 flex items-center justify-center shrink-0 overflow-hidden border border-white/10">
                        <i class="fa-solid ${isChannel ? 'fa-tower-broadcast' : 'fa-shield-halved'} text-[#00f3ff] text-xs"></i>
                    </div>
                    <div class="min-w-0">
                        <p class="text-xs font-bold text-theme-main truncate max-w-[130px]">${safeTitle}</p>
                        <p class="text-[9px] text-theme-muted font-mono flex items-center gap-1"><i class="fa-solid fa-user text-[8px]"></i> ${safeMembers}</p>
                    </div>
                </div>
                ${statusHtml}
            </div>
            <div class="h-16 w-full">${this.generateSparkline(chat.activity && chat.activity.length >= 2 ? chat.activity : [0, 0, 0, 0, 0], isChannel ? '#00f3ff' : '#39ff88')}</div>
            <div class="flex items-center justify-between border-t border-neutral-800 pt-2 font-mono">
                <span class="text-[9px]">${deltaHtml}</span>
                <button onclick="app.configureChat('${safeId}')" class="text-[9px] text-[#00f3ff] font-bold uppercase flex items-center gap-1 hover:text-white transition">
                    <i class="fa-solid fa-gear"></i> ${this.t('configure')}
                </button>
            </div>
        </div>`;
    },

    renderChatList(containerId, list, emptyMsg) {
        const el = document.getElementById(containerId);
        if (!el) return;
        const isChannel = containerId.includes('channel');
        const addBtnLabel = isChannel ? this.t('btn_connect_channel') : this.t('btn_connect_group');
        const targetType = isChannel ? 'channel' : 'group';

        if (!list || list.length === 0) {
            el.innerHTML = `
            <div class="glass-panel p-6 text-center text-xs text-neutral-500 font-mono space-y-3">
                <p>${this.escapeHtml(emptyMsg)}</p>
                <button onclick="app.openAddBot('${targetType}')" class="bg-[#00f3ff]/20 hover:bg-[#00f3ff]/30 text-[#00f3ff] border border-[#00f3ff]/40 px-4 py-2 rounded-xl text-xs font-bold transition-all active:scale-95 inline-flex items-center gap-2">
                    <i class="fa-solid fa-plus"></i> ${addBtnLabel}
                </button>
            </div>`;
            return;
        }
        el.innerHTML = list.map(c => this.chatCardTemplate(c)).join('');
    },

    renderChatListError(containerId, msg, retryFnName) {
        const el = document.getElementById(containerId);
        if (!el) return;
        el.innerHTML = `
        <div class="glass-panel p-6 text-center text-xs text-rose-400 font-mono space-y-3">
            <p>⚠️ ${msg}</p>
            <button onclick="app.${retryFnName}()" class="bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40 px-4 py-2 rounded-xl text-xs font-bold transition-all active:scale-95 inline-flex items-center gap-2">
                <i class="fa-solid fa-rotate"></i> ${this.t('btn_retry')}
            </button>
        </div>`;
    },

    subscriberTemplate(s) {
        const daysLeft = s.days_left ?? 0;
        const statusColor = daysLeft > 5 ? 'emerald-400' : (daysLeft > 0 ? 'amber-400' : 'rose-400');
        const daysLabel = daysLeft > 0 ? `${daysLeft} ${state.currentLang === 'es' ? 'días restantes' : 'days left'}` : (state.currentLang === 'es' ? 'Período de Gracia 🔴' : 'Grace Period 🔴');
        const safeUsername = this.escapeHtml(s.username);
        const safePlanName = this.escapeHtml(s.plan_name);
        const safePrice = this.escapeHtml(s.price);

        return `
        <div class="bg-black/60 p-3.5 rounded-xl border border-neutral-800 flex items-center justify-between font-mono">
            <div>
                <p class="text-white font-bold">@${safeUsername}</p>
                <p class="text-[10px] text-neutral-400">${safePlanName} (${safePrice} ⭐)</p>
            </div>
            <div class="text-right">
                <span class="text-${statusColor} font-bold text-xs">${daysLabel}</span>
            </div>
        </div>`;
    },

    renderSubscriberList(list) {
        const el = document.getElementById('watchdog-list');
        if (!el) return;
        if (!list || list.length === 0) {
            el.innerHTML = `<div class="glass-panel p-6 text-center text-xs text-neutral-500 font-mono">${this.t('no_subs')}</div>`;
            return;
        }
        el.innerHTML = list.map(s => this.subscriberTemplate(s)).join('');
    },

    populateSelect(id, list, emptyLabel) {
        const elements = document.querySelectorAll(`#${id}`);
        if (!elements.length) return;

        elements.forEach(sel => {
            if (!list || list.length === 0) {
                sel.innerHTML = `<option value="">${emptyLabel}</option>`;
                return;
            }
            sel.innerHTML = list.map(c => `<option value="${this.escapeHtml(c.id)}">${c.type === 'channel' ? '📢' : '🛡️'} ${this.escapeHtml(c.title)} (ID: ${this.escapeHtml(c.id)})</option>`).join('');
            if (state.selectedChatId) sel.value = state.selectedChatId;
        });
    },

    renderUserProfile(user) {
        const nameEl = document.getElementById('user-name');
        const handleEl = document.getElementById('user-handle');
        const idEl = document.getElementById('user-id-display');
        const imgEl = document.getElementById('avatar-img');
        const initialsEl = document.getElementById('avatar-initials');

        const dName = document.getElementById('drawer-user-name');
        const dHandle = document.getElementById('drawer-user-handle');
        const dImg = document.getElementById('drawer-avatar-img');
        const dInitials = document.getElementById('drawer-avatar-initials');

        const pName = document.getElementById('profile-name');
        const pHandle = document.getElementById('profile-handle');
        const pId = document.getElementById('profile-id');
        const pImg = document.getElementById('profile-img');
        const pInitials = document.getElementById('profile-initials');

        if (user) {
            const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim() || 'Comandante';
            const handle = user.username ? `@${user.username}` : `ID: ${user.id}`;
            const idText = `ID: ${user.id}`;
            const initials = ((user.first_name || 'U').charAt(0) + (user.last_name ? user.last_name.charAt(0) : '')).toUpperCase();

            if (nameEl) nameEl.innerText = fullName;
            if (handleEl) handleEl.innerText = handle;
            if (idEl) idEl.innerText = idText;

            if (dName) dName.innerText = fullName;
            if (dHandle) dHandle.innerText = handle;

            if (pName) pName.innerText = `${fullName} 👑`;
            if (pHandle) pHandle.innerText = handle;
            if (pId) pId.innerText = idText;

            if (user.photo_url) {
                [imgEl, dImg, pImg].forEach(img => {
                    if (img) { img.src = user.photo_url; img.classList.remove('hidden'); }
                });
                [initialsEl, dInitials, pInitials].forEach(init => {
                    if (init) init.classList.add('hidden');
                });
            } else {
                [initialsEl, dInitials, pInitials].forEach(init => {
                    if (init) init.innerText = initials;
                });
            }
        }
    },

    renderAffiliateLink(userId) {
        const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}`;
        const el = document.getElementById('affiliate-link-text');
        if (el) el.innerText = link;
    },

    async fetchTonBalance(address) {
        try {
            const res = await fetch(`https://toncenter.com/api/v2/getAddressBalance?address=${address}`);
            const data = await res.json();
            if (data.ok) return (parseInt(data.result) / 1e9).toFixed(2);
        } catch (e) { console.error('Error TON:', e); }
        return '0.00';
    },

    async updateWalletUI(account) {
        const btnText = document.getElementById('wallet-btn-text');
        const statusDot = document.getElementById('wallet-status-dot');
        if (!btnText || !statusDot) return;

        if (account) {
            const addr = account.address;
            btnText.innerText = state.currentLang === 'es' ? 'Cargando...' : 'Loading...';
            statusDot.className = 'w-2 h-2 rounded-full bg-emerald-400 mr-1.5 shadow-[0_0_8px_rgba(52,211,153,0.8)] animate-pulse';

            const balance = await this.fetchTonBalance(addr);
            const shortAddr = addr.slice(0, 4) + '...' + addr.slice(-4);
            btnText.innerText = `${shortAddr} | ${balance} TON`;
            statusDot.classList.remove('animate-pulse');
        } else {
            btnText.innerText = 'TON Wallet';
            statusDot.className = 'w-2 h-2 rounded-full bg-amber-500 mr-1.5 animate-pulse';
        }
    }
};