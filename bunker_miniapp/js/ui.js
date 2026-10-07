/* ==========================================================================
   THE BUNKER — COMMAND OS
   ui.js — Renderizado del DOM, Analítica en Vivo, Estudio de Canales, Planes de Membresía y Toasts
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

import { CONFIG, translations } from './config.js';
import { state } from './state.js';
import { tgApp } from './telegram.js';

// Colores e iconos por tipo de mensaje (claves de message_breakdown.types y del evento "message")
const KIND_META = {
    text:          { icon: '💬', color: '#00f3ff' },
    media:         { icon: '🖼️', color: '#ff00ff' },
    stickers_gifs: { icon: '🎭', color: '#fbbf24' },
    commands:      { icon: '⌨️', color: '#39ff88' },
    other:         { icon: '📦', color: '#94a3b8' }
};

// Color del texto del indicador de conexión del radar según el estado del cliente WebSocket.
// La propia etiqueta traducida lleva el emoji ("Conectado 🟢", "Reconectando 🟡"): no hay punto aparte.
const WS_STATUS_STYLE = {
    live:         'text-emerald-400',
    connecting:   'text-amber-400',
    reconnecting: 'text-amber-400',
    offline:      'text-rose-400',
    paused:       'text-neutral-400',
    denied:       'text-rose-400',
    idle:         'text-neutral-500'
};

// Tonos de los botones de acción de cada plan de membresía
const PLAN_TONES = {
    cyan:    'text-[#00f3ff] border-[#00f3ff]/40 bg-[#00f3ff]/10 hover:bg-[#00f3ff]/20',
    amber:   'text-amber-300 border-amber-400/40 bg-amber-400/10 hover:bg-amber-400/20',
    magenta: 'text-[#ff00ff] border-[#ff00ff]/40 bg-[#ff00ff]/10 hover:bg-[#ff00ff]/20',
    emerald: 'text-emerald-400 border-emerald-400/40 bg-emerald-400/10 hover:bg-emerald-400/20',
    rose:    'text-rose-400 border-rose-500/40 bg-rose-500/10 hover:bg-rose-500/20'
};

// Estados del indicador de guardado del Estudio de Canales
const STUDIO_STATUS_STYLE = {
    idle:       'text-neutral-500',
    dirty:      'text-amber-400',
    saving:     'text-[#00f3ff] animate-pulse',
    saved:      'text-emerald-400',
    mismatch:   'text-amber-400',
    invalid:    'text-rose-400',
    error:      'text-rose-400'
};

const TOAST_TONES = {
    info:    'bg-black/85 border-[#00f3ff]/40',
    success: 'bg-black/85 border-emerald-400/50',
    warn:    'bg-black/85 border-amber-400/50',
    error:   'bg-black/85 border-rose-500/50',
    level:   'bg-gradient-to-r from-[#1a0b2e]/95 to-[#0b1a2e]/95 border-[#ff00ff]/60'
};

const LIVE_CSS = `
@keyframes bk-toast-in { from { opacity: 0; transform: translateY(-10px) scale(.97); } to { opacity: 1; transform: none; } }
@keyframes bk-toast-out { to { opacity: 0; transform: translateY(-8px) scale(.97); } }
@keyframes bk-flash { 0% { text-shadow: 0 0 0 rgba(57,255,136,0); } 30% { text-shadow: 0 0 12px rgba(57,255,136,.9); color: #39ff88; } 100% { text-shadow: 0 0 0 rgba(57,255,136,0); } }
.bk-toast { animation: bk-toast-in .22s ease-out both; }
.bk-toast-out { animation: bk-toast-out .2s ease-in both; }
.bk-flash { animation: bk-flash .8s ease-out; }
.bk-hm-grid { display: grid; grid-template-columns: 24px repeat(24, minmax(0, 1fr)); gap: 2px; align-items: center; }
.bk-hm-cell { aspect-ratio: 1 / 1; border-radius: 2px; }
.bk-hm-day, .bk-hm-hour { font-size: 8px; line-height: 1; opacity: .7; font-family: ui-monospace, monospace; white-space: nowrap; }
@media (prefers-reduced-motion: reduce) { .bk-toast, .bk-toast-out, .bk-flash { animation: none; } }
`;

function byId(id) {
    return document.getElementById(id);
}

function clampPct(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(100, n));
}

export const ui = {
    t(key) {
        const dict = translations[state.currentLang] || {};
        return dict[key] || (translations.es && translations.es[key]) || key;
    },

    /** Traducción con variables: tf('toast_level_up_body', { name, level }) sustituye {name} y {level}. */
    tf(key, vars = {}) {
        return this.t(key).replace(/\{(\w+)\}/g, (match, name) => (vars[name] !== undefined ? String(vars[name]) : match));
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

    fmtNum(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '—';
        try {
            return new Intl.NumberFormat(state.currentLang === 'es' ? 'es-CO' : 'en-US').format(n);
        } catch (err) {
            return String(n);
        }
    },

    kindLabel(kind) {
        return this.t(`kind_${KIND_META[kind] ? kind : 'other'}`);
    },

    kindIcon(kind) {
        return (KIND_META[kind] || KIND_META.other).icon;
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

    /** Escribe texto plano (nunca HTML) en un elemento por id. Devuelve el elemento o null. */
    setText(id, value) {
        const el = byId(id);
        if (!el) return null;
        el.textContent = (value === null || value === undefined || value === '') ? '—' : String(value);
        return el;
    },

    setVisible(id, visible) {
        byId(id)?.classList.toggle('hidden', !visible);
    },

    /** Destello breve para indicar que un valor se actualizó en caliente. */
    flash(id) {
        const el = byId(id);
        if (!el || !el.classList) return;
        el.classList.remove('bk-flash');
        void el.offsetWidth;
        el.classList.add('bk-flash');
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
        const range = (max - min) === 0 ? 1 : (max - min);
        const stepX = width / (data.length - 1);
        const pts = data.map((v, i) => [i * stepX, height - ((v - min) / range) * (height - 16) - 8]);
        const line = pts.map((p, i) => (i === 0 ? `M${p[0].toFixed(1)},${p[1].toFixed(1)}` : `L${p[0].toFixed(1)},${p[1].toFixed(1)}`)).join(' ');
        const area = `${line} L${width},${height} L0,${height} Z`;
        const gid = `spark-${Math.random().toString(36).slice(2, 9)}`;
        return `<svg viewBox="0 0 ${width} ${height}" class="w-full h-full overflow-visible" preserveAspectRatio="none">
            <defs>
                <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="${c}" stop-opacity="0.35"/>
                    <stop offset="100%" stop-color="${c}" stop-opacity="0.0"/>
                </linearGradient>
            </defs>
            <path d="${area}" fill="url(#${gid})"/>
            <path d="${line}" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="filter:drop-shadow(0 0 5px ${c})"/>
        </svg>`;
    },

    chatCardTemplate(chat) {
        const isChannel = chat.type === 'channel';
        const licenseActive = chat.license_status === 'active';
        const safeId = this.escapeHtml(chat.id);
        const safeTitle = this.escapeHtml(chat.title);
        const safeMembers = this.escapeHtml(chat.members ?? '0');

        const statusHtml = licenseActive
            ? `<span class="text-emerald-400 font-bold text-[10px] shrink-0 font-mono">${this.t('license_active')}</span>`
            : `<button onclick="app.renewLicense('${safeId}')" class="bg-rose-500/20 text-rose-400 border border-rose-500/50 px-2.5 py-1 rounded-lg text-[10px] font-bold hover:bg-rose-500/30 active:scale-95 transition shrink-0">${this.t('license_renew')}</button>`;

        const deltaHtml = (chat.joined != null)
            ? `<span class="text-emerald-400 font-mono">+${this.escapeHtml(chat.joined)}</span> <span class="text-rose-400 ml-1.5 font-mono">-${this.escapeHtml(chat.left)}</span>`
            : `<span class="text-neutral-500 font-mono">—</span>`;

        // La analítica en vivo solo existe para grupos y supergrupos (los canales no generan mensajes rastreables).
        const analyticsBtn = isChannel ? '' : `
                    <button onclick="app.openAnalytics('${safeId}')" class="text-[9px] text-[#39ff88] font-bold uppercase flex items-center gap-1 hover:text-white transition">
                        <i class="fa-solid fa-chart-line"></i> ${this.t('nav_analytics')}
                    </button>`;

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
                <div class="flex items-center gap-3">${analyticsBtn}
                    <button onclick="app.configureChat('${safeId}')" class="text-[9px] text-[#00f3ff] font-bold uppercase flex items-center gap-1 hover:text-white transition">
                        <i class="fa-solid fa-gear"></i> ${this.t('configure')}
                    </button>
                </div>
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
        const isGrace = daysLeft <= 0;
        const statusColor = isGrace ? 'rose-400' : (daysLeft <= 3 ? 'amber-400' : 'emerald-400');
        const daysLabel = isGrace 
            ? (state.currentLang === 'es' ? 'Período de Gracia 🔴' : 'Grace Period 🔴')
            : `${daysLeft} ${state.currentLang === 'es' ? 'días' : 'days'}`;

        const safeUsername = this.escapeHtml(s.username || s.user_id);
        const safePlanName = this.escapeHtml(s.plan_name || 'Membresía');
        const safePrice = this.escapeHtml(s.price ?? 0);

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

    /**
     * Rellena todos los <select> con ese id. `kind` ('group' | 'channel') fija el texto del
     * placeholder; si se omite se deduce del id, como antes.
     */
    populateSelect(id, list, emptyLabel, kind) {
        const elements = document.querySelectorAll(`#${id}`);
        if (!elements.length) return;

        const isGroup = kind ? kind === 'group' : id.includes('group');
        const placeholderText = isGroup 
            ? (state.currentLang === 'es' ? '— Selecciona una comunidad —' : '— Select a community —')
            : (state.currentLang === 'es' ? '— Selecciona un canal —' : '— Select a channel —');

        elements.forEach(sel => {
            if (!list || list.length === 0) {
                sel.innerHTML = `<option value="">⚠️ ${emptyLabel}</option>`;
                return;
            }

            const optionsHtml = list.map(c => {
                const icon = c.type === 'channel' ? '📢' : '🛡️';
                return `<option value="${this.escapeHtml(c.id)}">${icon} ${this.escapeHtml(c.title)}</option>`;
            }).join('');

            sel.innerHTML = `<option value="">${placeholderText}</option>${optionsHtml}`;

            if (state.selectedChatId && list.some(item => String(item.id) === String(state.selectedChatId))) {
                sel.value = String(state.selectedChatId);
            }
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
        const el = document.getElementById('affiliate-link-text');
        if (!el) return;
        // Sin identidad de sesión no hay enlace: jamás se muestra el de otro operador.
        el.innerText = userId ? `https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}` : '—';
    },

    // ======================================================================
    // 📈 ANALÍTICA EN VIVO — constructores de HTML (puros, sin acceso al DOM)
    // ======================================================================

    /** Textos de todos los KPIs del snapshot, por id de elemento. */
    computeKpiTexts(data) {
        const s = data?.summary || {};
        const g = data?.growth || {};
        const c7 = g.cohort_7d || {};
        const c30 = g.cohort_30d || {};
        const pct = (cohort) => (cohort.retention_pct === null || cohort.retention_pct === undefined)
            ? '—'
            : `${cohort.retention_pct}%`;
        const members = (s.members_live !== null && s.members_live !== undefined) ? s.members_live : s.members_tracked;

        return {
            'an-kpi-dau': this.fmtNum(s.dau ?? 0),
            'an-kpi-wau': this.fmtNum(s.wau ?? 0),
            'an-kpi-mau': this.fmtNum(s.mau ?? 0),
            'an-kpi-stickiness': `${s.stickiness_pct ?? 0}%`,
            'an-kpi-members': this.fmtNum(members ?? 0),
            'an-kpi-members-sub': `${this.fmtNum(s.members_tracked ?? 0)} ${this.t('an_members_tracked')}`,
            'an-kpi-joins': `${this.fmtNum(g.new_members_7d ?? 0)} / ${this.fmtNum(g.new_members_30d ?? 0)}`,
            'an-kpi-retention-7d': pct(c7),
            'an-kpi-retention-7d-sub': `n=${this.fmtNum(c7.size ?? 0)}`,
            'an-kpi-retention-30d': pct(c30),
            'an-kpi-retention-30d-sub': `n=${this.fmtNum(c30.size ?? 0)}`
        };
    },

    /** Texto y color de la variación de mensajes frente a ayer a la misma hora. */
    computeMessageDelta(summary) {
        const delta = summary?.messages_delta_pct;
        if (delta === null || delta === undefined || !Number.isFinite(Number(delta))) {
            return { text: '—', tone: 'text-neutral-500' };
        }
        const value = Number(delta);
        return {
            text: `${value >= 0 ? '▲' : '▼'} ${Math.abs(value)}%`,
            tone: value >= 0 ? 'text-emerald-400' : 'text-rose-400'
        };
    },

    buildHeatmapHtml(heatmap) {
        const matrix = heatmap?.matrix;
        if (!Array.isArray(matrix) || matrix.length !== 7 || !heatmap.total) return '';

        const dayLabels = (state.currentLang === 'en' ? heatmap.days_en : heatmap.days) || [];
        const max = Math.max(1, Number(heatmap.max) || 0);
        const peak = heatmap.peak || null;
        const rows = [];

        for (let d = 0; d < 7; d++) {
            rows.push(`<span class="bk-hm-day">${this.escapeHtml(dayLabels[d] ?? '')}</span>`);
            const row = Array.isArray(matrix[d]) ? matrix[d] : [];
            for (let h = 0; h < 24; h++) {
                const value = Math.max(0, Number(row[h]) || 0);
                const alpha = value === 0 ? 0.05 : (0.14 + 0.86 * Math.sqrt(value / max));
                const isPeak = peak && peak.day_index === d && peak.hour === h;
                const bg = value === 0 ? `rgba(148,163,184,${alpha})` : `rgba(0,243,255,${alpha.toFixed(3)})`;
                const outline = isPeak ? 'outline:1px solid #ff00ff;outline-offset:0;' : '';
                const label = `${dayLabels[d] ?? ''} ${String(h).padStart(2, '0')}:00 · ${this.fmtNum(value)}`;
                rows.push(`<div class="bk-hm-cell" title="${this.escapeHtml(label)}" style="background:${bg};${outline}"></div>`);
            }
        }

        // Fila de horas: solo se rotulan 00, 06, 12, 18 y 23 para no saturar el ancho.
        rows.push('<span></span>');
        for (let h = 0; h < 24; h++) {
            rows.push(`<span class="bk-hm-hour">${[0, 6, 12, 18, 23].includes(h) ? String(h).padStart(2, '0') : ''}</span>`);
        }
        return `<div class="bk-hm-grid">${rows.join('')}</div>`;
    },

    buildHeatmapPeakText(heatmap) {
        const peak = heatmap?.peak;
        if (!peak) return '';
        const day = state.currentLang === 'en' ? peak.day_en : peak.day_es;
        return `${this.t('an_peak_label')}: ${day} ${String(peak.hour).padStart(2, '0')}:00 · ${this.fmtNum(peak.count)}`;
    },

    buildBreakdown(breakdown) {
        const types = Array.isArray(breakdown?.types) ? breakdown.types : [];
        const total = Number(breakdown?.total) || 0;
        if (!total) return { barHtml: '', legendHtml: '' };

        const barHtml = types
            .filter(item => Number(item.count) > 0)
            .map(item => {
                const color = (KIND_META[item.key] || KIND_META.other).color;
                return `<div style="flex:${Number(item.count)} 1 0%;background:${color}" title="${this.escapeHtml(item.key)}"></div>`;
            })
            .join('');

        const legendHtml = types.map(item => {
            const meta = KIND_META[item.key] || KIND_META.other;
            const label = state.currentLang === 'en' ? item.label_en : item.label_es;
            return `
            <div class="flex items-center justify-between gap-2 text-[11px]">
                <span class="flex items-center gap-1.5 min-w-0">
                    <span class="w-2 h-2 rounded-full shrink-0" style="background:${meta.color}"></span>
                    <span class="text-theme-main truncate">${this.escapeHtml(label || item.key)}</span>
                </span>
                <span class="font-mono text-neutral-400 shrink-0">${this.fmtNum(item.count)} · <strong class="text-theme-main">${this.escapeHtml(item.pct)}%</strong></span>
            </div>`;
        }).join('');

        return { barHtml, legendHtml };
    },

    buildLeaderboardHtml(list) {
        if (!Array.isArray(list) || list.length === 0) return '';
        const medals = ['🥇', '🥈', '🥉'];

        return list.slice(0, 10).map((u, idx) => {
            const rank = Number(u.rank) || (idx + 1);
            const handle = u.username ? `@${this.escapeHtml(u.username)} · ` : '';
            return `
            <div class="bg-black/40 p-2.5 rounded-xl border border-neutral-800 space-y-1.5" data-user-id="${this.escapeHtml(u.user_id ?? '')}">
                <div class="flex items-center justify-between gap-2">
                    <div class="flex items-center gap-2 min-w-0">
                        <span class="font-bold text-xs w-6 text-center shrink-0">${medals[rank - 1] || `#${rank}`}</span>
                        <div class="min-w-0">
                            <p class="text-theme-main font-bold text-xs truncate">${this.escapeHtml(u.name)}</p>
                            <span class="text-[9px] text-neutral-400 font-mono">${handle}${this.t('an_level')} ${this.escapeHtml(u.level)} · ${this.fmtNum(u.xp)} XP</span>
                        </div>
                    </div>
                    <span class="text-[#00f3ff] font-bold text-[11px] font-mono shrink-0">${this.fmtNum(u.messages_30d)} ${this.t('an_msgs_30d')}</span>
                </div>
                <div class="h-1 rounded-full bg-neutral-800 overflow-hidden">
                    <div class="h-full bg-gradient-to-r from-[#00f3ff] to-[#ff00ff]" style="width:${clampPct(u.level_progress_pct)}%"></div>
                </div>
            </div>`;
        }).join('');
    },

    sparkPlaceholder() {
        return '<div class="h-full flex items-center justify-center text-[10px] text-neutral-600 font-mono">—</div>';
    },

    // ======================================================================
    // 📈 ANALÍTICA EN VIVO — aplicación al DOM
    // ======================================================================
    ensureLiveStyles() {
        if (byId('bunker-live-styles')) return;
        const style = document.createElement('style');
        style.id = 'bunker-live-styles';
        style.textContent = LIVE_CSS;
        document.head.appendChild(style);
    },

    /** Muestra el estado vacío (sin comunidad), cargando, error o el contenido. */
    setAnalyticsPanel(panel) {
        this.setVisible('an-empty', panel === 'empty');
        this.setVisible('an-loading', panel === 'loading');
        this.setVisible('an-error', panel === 'error');
        this.setVisible('an-content', panel === 'content');
    },

    setAnalyticsLoading(loading) {
        // Si ya hay datos en pantalla, un refresco no debe taparlos con el cargador.
        if (loading && !state.liveAnalytics) this.setAnalyticsPanel('loading');
    },

    renderAnalyticsError(message, retryable = true) {
        this.setAnalyticsPanel('error');
        this.setText('an-error-msg', message);
        this.setVisible('an-error-retry', retryable);
    },

    resetAnalyticsView() {
        this.setAnalyticsPanel('empty');
        this.setText('an-updated', '');
        this.clearFeed();
    },

    renderAnalytics(data) {
        if (!data || typeof data !== 'object') return;
        this.ensureLiveStyles();
        this.setAnalyticsPanel('content');

        Object.entries(this.computeKpiTexts(data)).forEach(([id, text]) => this.setText(id, text));
        this.renderMessagesToday(data.summary);
        this.renderStarsKpis(data.summary);

        const daily = data.growth?.daily || {};
        const sparks = [
            ['an-spark-messages', daily.messages, '#00f3ff', 'an-spark-messages-total', data.summary?.messages_30d],
            ['an-spark-active', daily.active_users, '#39ff88', 'an-spark-active-peak', Array.isArray(daily.active_users) && daily.active_users.length ? Math.max(...daily.active_users) : 0],
            ['an-spark-growth', daily.new_members, '#ff00ff', 'an-spark-growth-total', data.growth?.new_members_30d]
        ];
        sparks.forEach(([containerId, series, color, labelId, headline]) => {
            const container = byId(containerId);
            if (container) container.innerHTML = this.generateSparkline(series, color) || this.sparkPlaceholder();
            this.setText(labelId, this.fmtNum(headline ?? 0));
        });

        const heatmapEl = byId('an-heatmap');
        if (heatmapEl) {
            heatmapEl.innerHTML = this.buildHeatmapHtml(data.heatmap)
                || `<p class="text-center text-[11px] text-neutral-500 font-mono py-6">${this.t('an_heatmap_empty')}</p>`;
        }
        this.setText('an-heatmap-peak', this.buildHeatmapPeakText(data.heatmap));

        const { barHtml, legendHtml } = this.buildBreakdown(data.message_breakdown);
        const barEl = byId('an-breakdown-bar');
        if (barEl) barEl.innerHTML = barHtml;
        const legendEl = byId('an-breakdown-legend');
        if (legendEl) {
            legendEl.innerHTML = legendHtml
                || `<p class="text-center text-[11px] text-neutral-500 font-mono py-2">${this.t('an_heatmap_empty')}</p>`;
        }

        const boardEl = byId('an-leaderboard');
        if (boardEl) {
            boardEl.innerHTML = this.buildLeaderboardHtml(data.leaderboard)
                || `<div class="glass-panel p-4 text-center text-xs text-neutral-500 font-mono">${this.t('an_lb_empty')}</div>`;
        }

        const stamp = data.generated_at_local ? `${data.generated_at_local}${data.timezone ? ` (${data.timezone})` : ''}` : '';
        this.setText('an-updated', stamp ? `${this.t('an_updated')}: ${stamp}` : '');
        this.renderVoiceCard();
    },

    renderMessagesToday(summary) {
        this.setText('an-kpi-messages-today', this.fmtNum(summary?.messages_today ?? 0));
        const delta = this.computeMessageDelta(summary);
        const el = this.setText('an-kpi-messages-delta', delta.text);
        if (el) el.className = `font-bold text-[10px] font-mono ${delta.tone}`;
    },

    renderStarsKpis(summary) {
        this.setText('an-kpi-stars-total', `${this.fmtNum(summary?.stars_total ?? 0)} ⭐`);
        this.setText('an-kpi-stars-today', this.fmtNum(summary?.stars_today ?? 0));
        this.setText('an-kpi-stars-30d', this.fmtNum(summary?.stars_30d ?? 0));
        this.setText('an-kpi-payments', this.fmtNum(summary?.payments_count ?? 0));
    },

    renderVoiceCard() {
        const voice = state.liveVoice || {};
        const active = Boolean(voice.active);
        this.setText('an-voice-status', active ? this.t('an_voice_live') : this.t('an_voice_idle'));
        const statusEl = byId('an-voice-status');
        if (statusEl) statusEl.className = `text-xs font-bold ${active ? 'text-emerald-400' : 'text-neutral-500'}`;
        const dot = byId('an-voice-dot');
        if (dot) dot.className = `w-2.5 h-2.5 rounded-full shrink-0 ${active ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.9)] animate-pulse' : 'bg-neutral-600'}`;
        this.setText('an-voice-count', `${this.fmtNum(voice.participants ?? 0)} ${this.t('an_voice_present')}`);
        this.setText('an-voice-mics', `${this.fmtNum(voice.mics ?? 0)} ${this.t('an_voice_mics')}`);
    },

    /** Indicador de conexión del radar (cabecera global + cabecera de la analítica). */
    setWsStatus(status) {
        const known = Object.prototype.hasOwnProperty.call(WS_STATUS_STYLE, status) ? status : 'idle';
        state.wsStatus = known;
        const label = this.t(`ws_${known}`);
        ['ws-status', 'an-ws'].forEach(prefix => {
            const text = byId(`${prefix}-label`);
            if (!text) return;
            text.textContent = label;
            text.className = `text-[10px] font-bold uppercase tracking-wide ${WS_STATUS_STYLE[known]}`;
        });
        byId('ws-status-pill')?.setAttribute('data-status', known);
    },

    /** Comunidad a la que está conectado el radar (junto al estado), para que cambiar de comunidad sea inequívoco. */
    setRadarTarget(title) {
        const el = byId('an-ws-chat');
        if (!el) return;
        el.textContent = title ? `· ${title}` : '';
        el.setAttribute('title', title || '');
    },

    renderToastToggle() {
        const icon = byId('an-toast-toggle-icon');
        if (icon) icon.className = state.liveToastsEnabled ? 'fa-solid fa-bell' : 'fa-solid fa-bell-slash';
        const btn = byId('an-toast-toggle');
        if (btn) btn.setAttribute('aria-pressed', state.liveToastsEnabled ? 'true' : 'false');
    },

    // ======================================================================
    // 💎 PLANES DE MEMBRESÍA DEL CANAL
    // ======================================================================

    /**
     * HTML de Telegram → HTML seguro para la vista previa. MISMA gramática que telegram_html.py
     * (normalize_telegram_html), que es lo que el backend publica: la vista previa no puede "arreglar"
     * algo que Telegram luego rechazaría o mostraría distinto.
     *  · Solo b, i, u, s, code sin atributos; los alias (strong, em, ins, strike, del) se canonicalizan.
     *  · Las entidades válidas de Telegram (&lt; &gt; &amp; &quot; &#NN; &#xHH;) se respetan
     *    (antes se escapaban dos veces: "A &amp; B" se veía literal en la vista previa).
     *  · Todo lo demás se escapa. Cierres huérfanos se descartan; los cruces se corrigen cerrando y
     *    reabriendo; lo abierto se cierra al final. Dentro de <code> no se abren otras etiquetas.
     */
    safeTelegramHtml(text) {
        const ALIASES = { b: 'b', strong: 'b', i: 'i', em: 'i', u: 'u', ins: 'u', s: 's', strike: 's', del: 's', code: 'code' };
        const TOKEN = /(<(\/?)(b|strong|i|em|u|ins|s|strike|del|code)>)|(&(?:lt|gt|amp|quot|#\d{1,7}|#x[0-9a-fA-F]{1,6});)/gi;
        const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const textPart = (s) => esc(s).replace(/\r?\n/g, '<br>');
        const src = text === null || text === undefined ? '' : String(text);
        const out = [];
        const stack = [];
        let pos = 0;
        let m;
        TOKEN.lastIndex = 0;
        while ((m = TOKEN.exec(src)) !== null) {
            out.push(textPart(src.slice(pos, m.index)));
            pos = TOKEN.lastIndex;
            if (m[4]) { out.push(m[4]); continue; }
            const closing = Boolean(m[2]);
            const name = ALIASES[m[3].toLowerCase()];
            const inCode = stack.includes('code');
            if (!closing) {
                if (inCode) { out.push(esc(m[1])); continue; }
                stack.push(name);
                out.push(`<${name}>`);
                continue;
            }
            if (!stack.includes(name)) { if (inCode) out.push(esc(m[1])); continue; }
            if (inCode && name !== 'code') { out.push(esc(m[1])); continue; }
            const reopen = [];
            while (stack.length) {
                const top = stack.pop();
                out.push(`</${top}>`);
                if (top === name) break;
                reopen.push(top);
            }
            for (let k = reopen.length - 1; k >= 0; k--) { stack.push(reopen[k]); out.push(`<${reopen[k]}>`); }
        }
        out.push(textPart(src.slice(pos)));
        while (stack.length) out.push(`</${stack.pop()}>`);
        return out.join('');
    },

    /** Botón de acción rápida de un plan: glifo + etiqueta diminuta (5 caben en una fila de 360 px). */
    planActionButton({ action, glyph, label, tip, tone, onclick, disabled = false, spinning = false }) {
        const icon = spinning
            ? '<i class="fa-solid fa-spinner fa-spin text-sm leading-none"></i>'
            : `<span class="text-base leading-none">${glyph}</span>`;
        return `
        <button type="button" data-action="${action}" onclick="${onclick}" title="${this.escapeHtml(tip)}" aria-label="${this.escapeHtml(tip)}"${disabled ? ' disabled' : ''}
            class="flex flex-col items-center justify-center gap-1 py-2 px-1 rounded-xl border transition active:scale-95 disabled:opacity-40 disabled:cursor-wait disabled:active:scale-100 ${PLAN_TONES[tone] || PLAN_TONES.cyan}">
            ${icon}
            <span class="text-[8px] font-bold uppercase leading-none truncate max-w-full">${this.escapeHtml(label)}</span>
        </button>`;
    },

    /** Barra de confirmación inline (eliminar / difundir): sustituye a la botonera hasta confirmar o cancelar. */
    planConfirmBar(plan, action) {
        const isDelete = action === 'delete';
        const tone = isDelete ? 'rose' : 'magenta';
        const border = isDelete ? 'border-rose-500/40 bg-rose-500/10' : 'border-[#ff00ff]/40 bg-[#ff00ff]/10';
        const text = this.tf(isDelete ? 'plans_confirm_delete' : 'plans_confirm_broadcast', { name: plan.name });
        const yes = this.t(isDelete ? 'plans_confirm_delete_yes' : 'plans_confirm_broadcast_yes');
        const call = isDelete ? `app.deletePlan(${plan.plan_id}, true)` : `app.broadcastPlan(${plan.plan_id}, true)`;
        const solid = isDelete ? 'bg-rose-500/90 text-white' : 'bg-[#ff00ff] text-black';
        return `
        <div role="alertdialog" data-confirm="${action}" class="rounded-xl border ${border} p-2.5 space-y-2.5">
            <p class="text-[11px] text-theme-main leading-snug break-words">${this.escapeHtml(text)}</p>
            <div class="grid grid-cols-2 gap-2">
                <button type="button" data-action="cancel" onclick="app.cancelPlanConfirm()" class="py-2 rounded-xl border border-neutral-600 text-neutral-300 text-[10px] font-bold uppercase transition active:scale-95 hover:bg-white/5">${this.escapeHtml(this.t('plans_confirm_no'))}</button>
                <button type="button" data-action="confirm" onclick="${call}" class="py-2 rounded-xl ${solid} text-[10px] font-extrabold uppercase transition active:scale-95" data-tone="${tone}">${this.escapeHtml(yes)}</button>
            </div>
        </div>`;
    },

    /** Fila con el enlace de compra generado y su botón Copiar. */
    planLinkRow(plan, link) {
        return `
        <div data-role="plan-link" class="rounded-xl bg-black/50 border border-emerald-400/30 p-2.5 space-y-1.5">
            <p class="text-[9px] uppercase font-bold text-emerald-400 tracking-wide">🔗 ${this.escapeHtml(this.t('plans_link_label'))}</p>
            <div class="flex items-center gap-2">
                <span class="flex-1 min-w-0 truncate font-mono text-[10px] text-neutral-200 select-all">${this.escapeHtml(link)}</span>
                <button type="button" data-action="copy" onclick="app.copyPlanLink(${plan.plan_id})" class="shrink-0 px-2.5 py-1.5 rounded-lg bg-emerald-400/15 border border-emerald-400/40 text-emerald-400 text-[9px] font-extrabold uppercase transition active:scale-95 hover:bg-emerald-400/25">${this.escapeHtml(this.t('plans_btn_copy'))}</button>
            </div>
            <p class="text-[9px] text-neutral-500 leading-snug">${this.escapeHtml(this.t('plans_link_hint'))}</p>
        </div>`;
    },

    /** Tarjeta completa de un plan. ctx: { busyAction, confirm, link }. */
    buildPlanCard(plan, ctx = {}) {
        const id = plan.plan_id;
        const active = Boolean(plan.is_active);
        const busyAction = ctx.busyAction || null;
        const busy = Boolean(busyAction);

        const badge = active
            ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
            : 'bg-rose-500/15 text-rose-400 border-rose-500/40';
        const bar = active ? 'border-l-emerald-400' : 'border-l-rose-500';
        const mediaChip = plan.has_media
            ? `<span class="px-2 py-0.5 rounded-lg bg-black/50 border border-neutral-700 text-neutral-300">🖼️ ${this.escapeHtml(this.t('plans_media_' + plan.media_type))}</span>`
            : '';

        const actions = ctx.confirm
            ? this.planConfirmBar(plan, ctx.confirm)
            : `<div class="grid grid-cols-5 gap-1.5">
                ${this.planActionButton({ action: 'preview', glyph: '👁️', label: this.t('plans_btn_preview'), tip: this.t('plans_tip_preview'), tone: 'cyan', onclick: `app.previewPlan(${id})` })}
                ${this.planActionButton({ action: 'toggle', glyph: '🔄', label: this.t(active ? 'plans_btn_pause' : 'plans_btn_activate'), tip: this.t(active ? 'plans_tip_pause' : 'plans_tip_activate'), tone: 'amber', onclick: `app.togglePlanStatus(${id})`, disabled: busy, spinning: busyAction === 'toggle' })}
                ${this.planActionButton({ action: 'broadcast', glyph: '📢', label: this.t('plans_btn_broadcast'), tip: this.t('plans_tip_broadcast'), tone: 'magenta', onclick: `app.broadcastPlan(${id})`, disabled: busy, spinning: busyAction === 'broadcast' })}
                ${this.planActionButton({ action: 'link', glyph: '🔗', label: this.t(ctx.link ? 'plans_btn_copy' : 'plans_btn_link'), tip: this.t(ctx.link ? 'plans_tip_copy' : 'plans_tip_link'), tone: 'emerald', onclick: `app.generateInviteLink(${id})`, disabled: busy, spinning: busyAction === 'link' })}
                ${this.planActionButton({ action: 'delete', glyph: '🗑️', label: this.t('plans_btn_delete'), tip: this.t('plans_tip_delete'), tone: 'rose', onclick: `app.deletePlan(${id})`, disabled: busy, spinning: busyAction === 'delete' })}
            </div>`;

        return `
        <article id="plan-card-${id}" data-plan-id="${id}" data-status="${active ? 'active' : 'paused'}"${busy ? ' aria-busy="true"' : ''} class="glass-panel p-3.5 space-y-3 border-l-2 ${bar}">
            <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                    <h4 class="text-sm font-extrabold text-theme-main truncate">💎 ${this.escapeHtml(plan.name)}</h4>
                    <div class="flex flex-wrap items-center gap-1.5 mt-1.5 font-mono text-[10px]">
                        <span class="px-2 py-0.5 rounded-lg bg-black/50 border border-neutral-700 text-neutral-300">⏳ ${this.escapeHtml(this.tf('plans_days', { n: this.fmtNum(plan.duration_days) }))}</span>
                        <span class="px-2 py-0.5 rounded-lg bg-black/50 border border-amber-400/30 text-amber-300 font-bold">⭐ ${this.escapeHtml(this.fmtNum(plan.stars_price))} XTR</span>
                        ${mediaChip}
                    </div>
                </div>
                <span data-role="plan-status" class="shrink-0 px-2 py-1 rounded-lg border text-[10px] font-bold ${badge}">${this.escapeHtml(this.t(active ? 'plans_status_active' : 'plans_status_paused'))}</span>
            </div>
            ${ctx.link ? this.planLinkRow(plan, ctx.link) : ''}
            ${actions}
        </article>`;
    },

    /** Resumen bajo el título: "N activos · M en total · K suscriptores activos". */
    renderChannelPlansSummary(list, summary) {
        const el = byId('channel-plans-count');
        if (!el) return;
        if (!Array.isArray(list)) {
            el.textContent = '';
            return;
        }
        const active = summary && Number.isFinite(Number(summary.active_count)) ? Number(summary.active_count) : list.filter(p => p.is_active).length;
        const total = summary && Number.isFinite(Number(summary.total)) ? Number(summary.total) : list.length;
        let text = this.tf('plans_count', { active: this.fmtNum(active), total: this.fmtNum(total) });
        if (summary && summary.subscribers !== null && summary.subscribers !== undefined && Number.isFinite(Number(summary.subscribers))) {
            text += ` · ${this.tf('plans_subscribers', { n: this.fmtNum(summary.subscribers) })}`;
        }
        el.textContent = text;
    },

    /**
     * Dibuja el listado de planes del canal en #channel-plans-list.
     * @param {Array} plansList  Planes del servidor: { plan_id, name, duration_days, stars_price, status, is_active, ... }
     * @param {object} [opts]    { busy: {planId: acción}, confirm: {planId, action}, links: {planId: url}, summary }
     */
    renderChannelPlans(plansList, opts = {}) {
        const host = byId('channel-plans-list');
        if (!host) return;
        const busy = opts.busy || {};
        const links = opts.links || {};
        const confirm = opts.confirm || null;
        const list = (Array.isArray(plansList) ? plansList : []).filter(p => p && Number.isInteger(Number(p.plan_id)));

        this.renderChannelPlansSummary(list, opts.summary);

        if (list.length === 0) {
            host.innerHTML = `
            <div class="rounded-2xl border border-dashed border-neutral-700 p-5 text-center space-y-2">
                <div class="text-2xl">💎</div>
                <p class="text-[11px] text-neutral-400 leading-relaxed">${this.escapeHtml(this.t('plans_empty'))}</p>
            </div>`;
            return;
        }

        host.innerHTML = list.map(plan => {
            const normalized = { ...plan, plan_id: Number(plan.plan_id) };
            return this.buildPlanCard(normalized, {
                busyAction: busy[normalized.plan_id] || null,
                confirm: confirm && Number(confirm.planId) === normalized.plan_id ? confirm.action : null,
                link: links[normalized.plan_id] || null
            });
        }).join('');
    },

    /** Estados sin lista: idle (sin canal), loading y error (con reintento). */
    renderChannelPlansState(kind, message = '') {
        const host = byId('channel-plans-list');
        if (!host) return;
        this.renderChannelPlansSummary(null);

        if (kind === 'loading') {
            host.innerHTML = `<div class="rounded-2xl bg-black/30 border border-neutral-800 p-5 text-center text-[11px] text-[#00f3ff] font-mono animate-pulse"><i class="fa-solid fa-spinner fa-spin mr-1.5"></i>${this.escapeHtml(this.t('plans_loading'))}</div>`;
        } else if (kind === 'error') {
            host.innerHTML = `
            <div class="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-5 text-center space-y-3">
                <p class="text-[11px] text-rose-300 leading-relaxed">⚠️ ${this.escapeHtml(message || this.t('plans_load_error'))}</p>
                <button type="button" data-action="retry" onclick="app.refreshChannelPlans()" class="bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40 px-4 py-2 rounded-xl text-[11px] font-bold transition active:scale-95 inline-flex items-center gap-2"><i class="fa-solid fa-rotate"></i> ${this.escapeHtml(this.t('btn_retry'))}</button>
            </div>`;
        } else {
            host.innerHTML = `<div class="rounded-2xl border border-dashed border-neutral-800 p-5 text-center text-[11px] text-neutral-500 font-mono">${this.escapeHtml(this.t('plans_select_channel'))}</div>`;
        }
    },

    /** Vista previa de la tarjeta comercial tal como la verán los suscriptores en Telegram (solo lectura). */
    renderPlanPreview(plan) {
        const body = byId('plan-preview-body');
        if (!body || !plan) return;
        const stars = Number(plan.stars_price) || 0;
        const media = plan.has_media
            ? `<div class="h-28 bg-gradient-to-br from-[#00f3ff]/20 via-[#1f2a37] to-[#ff00ff]/20 flex items-center justify-center text-neutral-300 text-xs font-mono">🖼️ ${this.escapeHtml(this.tf('plans_preview_media', { type: this.t('plans_media_' + plan.media_type) }))}</div>`
            : '';
        const promo = plan.promo_text
            ? `<div class="text-[12px] text-neutral-200 leading-relaxed break-words">${this.safeTelegramHtml(plan.promo_text)}</div>`
            : '';
        const resource = plan.target_link
            ? `<div class="py-2 rounded-lg bg-white/10 text-center text-[11px] font-semibold text-[#8ab4f8]">${this.escapeHtml(this.t('plans_preview_resource'))}</div>`
            : '';

        body.innerHTML = `
        <div data-role="plan-preview" class="rounded-2xl bg-[#17212b] border border-white/10 overflow-hidden text-left">
            ${media}
            <div class="p-3.5 space-y-2.5">
                <p class="text-[13px] font-extrabold text-white break-words">💎 ${this.escapeHtml(plan.name)}</p>
                <p class="text-[12px] text-neutral-200">⏳ <b>${this.escapeHtml(this.t('plans_preview_duration'))}:</b> ${this.escapeHtml(this.tf('plans_days', { n: this.fmtNum(plan.duration_days) }))}</p>
                <p class="text-[12px] text-neutral-200">⭐ <b>${this.escapeHtml(this.t('plans_preview_price'))}:</b> ${this.escapeHtml(this.fmtNum(stars))} XTR</p>
                ${promo}
                <p class="text-[10px] text-neutral-400 italic">🛡️ Cloud Media Management</p>
            </div>
            <div class="px-3.5 pb-3.5 space-y-1.5">
                <div class="py-2 rounded-lg bg-white/10 text-center text-[11px] font-semibold text-[#8ab4f8]">${this.escapeHtml(this.tf('plans_preview_subscribe', { n: this.fmtNum(stars) }))}</div>
                ${resource}
            </div>
        </div>`;
        this.setVisible('modal-plan-preview', true);
    },

    closePlanPreview() {
        this.setVisible('modal-plan-preview', false);
        const body = byId('plan-preview-body');
        if (body) body.innerHTML = '';
    },

    // ======================================================================
    // 🎬 ESTUDIO DE CANALES — formulario reactivo
    // ======================================================================
    /**
     * Campos del Estudio que se guardan de forma reactiva: la difusión automática de promoción (v8.2).
     * Enlace VIP, tarifa y duración ya no son "ajustes sueltos" del canal: viven en cada plan que crea el
     * Generador de Planes (v8.3) dentro de channel_plans, que es lo que cobra y entrega el bot.
     */
    STUDIO_FIELDS: {
        broadcast_target:   'studio-broadcast-target',
        broadcast_interval: 'studio-broadcast-interval',
        promo_text:         'studio-promo-text'
    },

    /** Valores crudos (texto) de la difusión del Estudio. */
    readStudioForm() {
        const read = (id) => byId(id)?.value ?? '';
        const f = this.STUDIO_FIELDS;
        return {
            broadcast_target: read(f.broadcast_target),
            broadcast_interval: read(f.broadcast_interval),
            promo_text: read(f.promo_text)
        };
    },

    // ======================================================================
    // ➕ GENERADOR DE PLANES (v8.3)
    // ======================================================================
    PLAN_FORM_FIELDS: {
        plan_name:     'plan-form-name',
        stars_price:   'plan-form-price',
        duration_days: 'plan-form-days',
        target_link:   'plan-form-link',
        promo_text:    'plan-form-promo'
    },

    readPlanForm() {
        const read = (id) => byId(id)?.value ?? '';
        const f = this.PLAN_FORM_FIELDS;
        return Object.fromEntries(Object.entries(f).map(([key, id]) => [key, read(id)]));
    },

    /** Restablece el formulario a sus valores por defecto (también con el foco: es una acción explícita). */
    resetPlanForm() {
        const defaults = {
            plan_name: '', target_link: '', promo_text: '',
            stars_price: String(CONFIG.PLAN_FORM?.DEFAULT_PRICE ?? 150),
            duration_days: String(CONFIG.PLAN_FORM?.DEFAULT_DAYS ?? 30)
        };
        Object.entries(this.PLAN_FORM_FIELDS).forEach(([key, id]) => {
            const el = byId(id);
            if (el) el.value = defaults[key];
            this.setFieldError(id, '');
        });
        this.updatePlanPromoCounter();
        this.renderPlanFormResult(null);
    },

    clearPlanFormErrors() {
        Object.values(this.PLAN_FORM_FIELDS).forEach(id => this.setFieldError(id, ''));
    },

    updatePlanPromoCounter() {
        const area = byId(this.PLAN_FORM_FIELDS.promo_text);
        const counter = byId('plan-form-promo-counter');
        if (!area || !counter) return;
        const max = CONFIG.PLAN_FORM?.PROMO_MAX || 1000;
        const len = area.value.length;
        counter.textContent = `${len}/${max}`;
        counter.className = `text-[9px] font-mono ${len > max ? 'text-rose-400' : len > max * 0.9 ? 'text-amber-300' : 'text-neutral-500'}`;
    },

    /** Botones del generador ocupados (crear / difundir) con spinner en el botón principal. */
    setPlanFormBusy(busy) {
        ['pf-create-btn', 'pf-broadcast-btn', 'pf-clear-btn', 'pf-preview-btn'].forEach(id => this.setButtonBusy(id, busy));
        const icon = byId('pf-create-icon');
        if (icon) icon.innerHTML = busy ? '<i class="fa-solid fa-spinner fa-spin"></i>' : '💾';
        this.setText('pf-create-label', this.t(busy ? 'pf_creating' : 'pf_btn_create'));
    },

    /**
     * Resultado de la creación: enlace de compra y, si el bot puede invitar, el enlace de verificación de
     * un solo uso (solo para el propietario). null lo oculta.
     */
    renderPlanFormResult(result) {
        const box = byId('plan-form-result');
        if (!box) return;
        if (!result || !result.plan) {
            box.innerHTML = '';
            box.classList.add('hidden');
            return;
        }
        const rows = [];
        if (result.purchase_link) {
            rows.push(`<p class="text-emerald-300 break-all">🔗 <span class="select-all">${this.escapeHtml(result.purchase_link)}</span></p>`);
        }
        const check = result.delivery || {};
        if (check.ready && check.invite_link) {
            rows.push(`<p class="text-neutral-400">${this.escapeHtml(this.t('pf_check_link'))}: <span class="text-[#00f3ff] select-all break-all">${this.escapeHtml(check.invite_link)}</span></p>`);
        } else {
            rows.push(`<p class="text-amber-300">⚠️ ${this.escapeHtml(this.t('pf_toast_created_warn'))}</p>`);
        }
        box.className = `rounded-xl border p-2.5 space-y-1 text-[10px] font-mono ${check.ready ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-amber-400/40 bg-amber-400/5'}`;
        box.innerHTML = `<p class="text-white font-bold">💎 ${this.escapeHtml(result.plan.name)} · ${this.escapeHtml(this.fmtNum(result.plan.stars_price))} ⭐ · ${this.escapeHtml(this.tf('plans_days', { n: this.fmtNum(result.plan.duration_days) }))}</p>${rows.join('')}`;
    },

    // ======================================================================
    // 🎛️ CONSOLA DE PROGRAMACIÓN 1:1 (v8.3)
    // ======================================================================
    /** Campos de cada módulo. Los de tipo 'switch' se aplican al instante; el resto con "Guardar". */
    CONFIG_LAYOUT: {
        aduana: {
            captcha_enabled: 'switch', captcha_mode: 'select', captcha_timeout: 'int', custom_welcome: 'text'
        },
        acoustic: {
            autolower_enabled: 'switch', autolower_pct: 'int', shield_enabled: 'switch', micvip_price: 'int', speaker_price: 'int'
        },
        tips: {
            tips_enabled: 'switch', tips_presets: 'presets', custom_tips_allowed: 'switch'
        },
        perimeter: {
            linklock_enabled: 'switch', antiflood_enabled: 'switch', antiflood_rate: 'int', antiflood_window: 'int',
            service_cleaner_enabled: 'switch'
        }
    },

    configSectionOf(field) {
        return Object.keys(this.CONFIG_LAYOUT).find(sec => Object.prototype.hasOwnProperty.call(this.CONFIG_LAYOUT[sec], field)) || null;
    },

    /** Interruptor visual (role="switch"). busy=true muestra el pulso mientras viaja la petición. */
    setConfigSwitch(field, on, { busy = false, disabled = false } = {}) {
        const btn = byId(`cfg-${field}`);
        if (!btn) return;
        const knob = btn.querySelector('.cfg-switch-knob');
        btn.setAttribute('aria-checked', on ? 'true' : 'false');
        btn.disabled = Boolean(disabled || busy);
        btn.classList.toggle('bg-[#00f3ff]/30', Boolean(on));
        btn.classList.toggle('border-[#00f3ff]', Boolean(on));
        btn.classList.toggle('bg-neutral-800', !on);
        btn.classList.toggle('border-neutral-600', !on);
        btn.classList.toggle('animate-pulse', Boolean(busy));
        if (knob) {
            knob.classList.toggle('left-0.5', !on);
            knob.classList.toggle('left-6', Boolean(on));
            knob.classList.toggle('bg-[#00f3ff]', Boolean(on));
            knob.classList.toggle('shadow-[0_0_10px_#00f3ff]', Boolean(on));
            knob.classList.toggle('bg-neutral-400', !on);
        }
    },

    getConfigSwitch(field) {
        return byId(`cfg-${field}`)?.getAttribute('aria-checked') === 'true';
    },

    updateAutolowerLabel() {
        const range = byId('cfg-autolower_pct');
        this.setText('cfg-autolower_pct-value', `${range ? range.value : 2}%`);
    },

    updateWelcomeCounter() {
        const area = byId('cfg-custom_welcome');
        const counter = byId('cfg-custom_welcome-counter');
        if (!area || !counter) return;
        const max = CONFIG.CHAT_CONFIG?.WELCOME_MAX || 1000;
        counter.textContent = `${area.value.length}/${max}`;
        counter.className = `text-[9px] font-mono ${area.value.length > max ? 'text-rose-400' : 'text-neutral-500'}`;
    },

    /**
     * Pinta la configuración recibida del servidor. `skipSections` protege los módulos con ediciones sin
     * guardar, y un campo con el foco nunca se pisa (el operador puede estar escribiendo).
     */
    renderChatConfiguration(config, { skipSections = [] } = {}) {
        if (!config) return;
        Object.entries(this.CONFIG_LAYOUT).forEach(([section, fields]) => {
            if (skipSections.includes(section)) return;
            const values = config[section] || {};
            Object.entries(fields).forEach(([field, kind]) => {
                if (!Object.prototype.hasOwnProperty.call(values, field)) return;
                const value = values[field];
                if (kind === 'switch') {
                    this.setConfigSwitch(field, Boolean(value));
                    return;
                }
                const el = byId(`cfg-${field}`);
                if (!el || document.activeElement === el) return;
                el.value = kind === 'presets' ? (Array.isArray(value) ? value.join(', ') : String(value ?? '')) : String(value ?? '');
                this.setFieldError(`cfg-${field}`, '');
            });
        });
        this.updateAutolowerLabel();
        this.updateWelcomeCounter();
    },

    /** Valores crudos de los campos NO interruptor de un módulo. */
    readConfigSection(section) {
        const fields = this.CONFIG_LAYOUT[section] || {};
        const out = {};
        Object.entries(fields).forEach(([field, kind]) => {
            if (kind === 'switch') return;
            out[field] = byId(`cfg-${field}`)?.value ?? '';
        });
        return out;
    },

    /** Estado del módulo: idle | dirty | saving | saved | invalid | error | remote. */
    setConfigSectionStatus(section, kind, text = '') {
        const el = byId(`cfg-status-${section}`);
        if (!el) return;
        const tone = {
            dirty: 'text-amber-300', saving: 'text-[#00f3ff]', saved: 'text-emerald-400',
            invalid: 'text-rose-400', error: 'text-rose-400', remote: 'text-[#ff00ff]'
        }[kind] || 'text-neutral-500';
        el.textContent = text;
        el.className = `text-[9px] font-mono ${tone}`;
    },

    setConfigSectionBusy(section, busy) {
        this.setButtonBusy(`cfg-save-${section}`, busy);
        this.setText(`cfg-save-label-${section}`, this.t(busy ? 'cfg_saving' : 'cfg_save'));
    },

    /**
     * Estado global de la consola: 'idle' (sin comunidad), 'loading' o 'ready'. Fuera de 'ready' todos los
     * controles quedan deshabilitados: nunca se edita sobre valores que no vinieron del servidor.
     */
    setConsoleState(kind, message = '') {
        const ready = kind === 'ready';
        document.querySelectorAll('#cfg-console .cfg-input, #cfg-console .cfg-switch, #cfg-console [id^="cfg-save-"]').forEach(el => {
            if (el.tagName === 'BUTTON' && el.id.startsWith('cfg-save-label')) return;
            el.disabled = !ready;
        });
        const label = byId('cfg-console-state');
        if (label) {
            label.textContent = message || (kind === 'loading' ? this.t('cfg_loading') : kind === 'idle' ? this.t('cfg_pick') : '');
            label.className = `text-[9px] font-mono ${kind === 'loading' ? 'text-[#00f3ff] animate-pulse' : 'text-neutral-500'}`;
        }
        if (!ready) Object.keys(this.CONFIG_LAYOUT).forEach(sec => this.setConfigSectionStatus(sec, 'idle'));
    },

    /** Contador de caracteres del texto promocional (rojo al superar el límite). */
    updatePromoCounter() {
        const area = byId(this.STUDIO_FIELDS.promo_text);
        const counter = byId('studio-promo-counter');
        if (!area || !counter) return;
        const max = CONFIG.STUDIO?.PROMO_MAX || 1000;
        const len = area.value.length;
        counter.textContent = `${len}/${max}`;
        counter.className = `text-[9px] font-mono ${len > max ? 'text-rose-400' : len > max * 0.9 ? 'text-amber-300' : 'text-neutral-500'}`;
    },

    /** Indicador de la difusión automática guardada en el servidor. */
    setBroadcastStatus(cfg) {
        const el = byId('studio-broadcast-status');
        if (!el) return;
        const on = Boolean(cfg && cfg.broadcast_enabled);
        el.textContent = on ? this.tf('bc_status_on', { h: cfg.broadcast_interval }) : this.t('bc_status_off');
        el.className = `text-[9px] font-mono shrink-0 ${on ? 'text-emerald-400' : 'text-neutral-500'}`;
    },

    /**
     * Vista previa de la difusión personalizada. El texto pasa por safeTelegramHtml (misma gramática que
     * telegram_html.py en el backend): lo que se ve aquí es exactamente lo que se publica.
     */
    renderBroadcastPreview({ promoText = '', targetLabel = '', interval = 12, scheduled = false } = {}) {
        const body = byId('broadcast-preview-body');
        if (!body) return;
        const target = targetLabel
            ? this.tf('bc_preview_target', { target: targetLabel })
            : this.t('bc_preview_target_self');
        const schedule = scheduled ? this.tf('bc_preview_schedule', { h: interval }) : this.t('bc_preview_schedule_off');
        body.innerHTML = `
        <div class="space-y-2.5">
            <div class="flex flex-wrap gap-1.5 text-[9px] font-mono">
                <span class="px-2 py-0.5 rounded-lg bg-black/50 border border-neutral-700 text-neutral-300">📍 ${this.escapeHtml(target)}</span>
                <span class="px-2 py-0.5 rounded-lg bg-black/50 border border-neutral-700 text-neutral-300">⏱️ ${this.escapeHtml(schedule)}</span>
            </div>
            <div data-role="broadcast-preview" class="rounded-2xl bg-[#17212b] border border-white/10 overflow-hidden text-left">
                <div class="p-3.5 space-y-2.5">
                    <div class="text-[12px] text-neutral-200 leading-relaxed break-words">${this.safeTelegramHtml(promoText)}</div>
                    <p class="text-[10px] text-neutral-400 italic">🛡️ Cloud Media Management</p>
                </div>
                <div class="px-3.5 pb-3.5">
                    <div class="py-2 rounded-lg bg-white/10 text-center text-[11px] font-semibold text-[#8ab4f8]">${this.escapeHtml(this.t('bc_preview_buy'))}</div>
                </div>
            </div>
            <p class="text-[9px] text-neutral-500 leading-snug">${this.escapeHtml(this.t('bc_preview_buy_note'))}</p>
        </div>`;
        this.setVisible('modal-broadcast-preview', true);
    },

    closeBroadcastPreview() {
        this.setVisible('modal-broadcast-preview', false);
        const body = byId('broadcast-preview-body');
        if (body) body.innerHTML = '';
    },

    /** Deshabilita los botones de difusión mientras se publica (evita dobles envíos). */
    setBroadcastBusy(busy) {
        ['bc-send-btn', 'bc-preview-send-btn', 'bc-clear-btn'].forEach(id => this.setButtonBusy(id, busy));
        const icon = byId('bc-send-icon');
        if (icon) icon.innerHTML = busy ? '<i class="fa-solid fa-spinner fa-spin text-sm"></i>' : '📢';
    },

    /** Botón ocupado genérico (disabled + aria-busy). */
    setButtonBusy(id, busy) {
        const btn = byId(id);
        if (!btn) return;
        btn.disabled = Boolean(busy);
        btn.setAttribute('aria-busy', busy ? 'true' : 'false');
    },

    // ======================================================================
    // 🧾 CANAL DE REGISTRO — modal nativo (v8.2)
    // ======================================================================
    openLogChannelView({ community = '', current = '' } = {}) {
        this.setText('log-channel-community', community || '—');
        this.setText('log-channel-current', current || this.t('logm_none'));
        const input = byId('log-channel-input');
        if (input) input.value = current || '';
        this.setFieldError('log-channel-input', '');
        this.setLogChannelBusy(false);
        this.setVisible('modal-log-channel', true);
        // En móviles el foco abre el teclado al instante; un pequeño retraso evita saltos de la animación.
        if (input) setTimeout(() => { try { input.focus({ preventScroll: true }); } catch (err) { input.focus(); } }, 120);
    },

    closeLogChannelView() {
        const input = byId('log-channel-input');
        if (input) input.blur();
        this.setVisible('modal-log-channel', false);
        this.setLogChannelBusy(false);
    },

    isLogChannelOpen() {
        const el = byId('modal-log-channel');
        return Boolean(el && !el.classList.contains('hidden'));
    },

    setLogChannelBusy(busy) {
        this.setButtonBusy('log-channel-save-btn', busy);
        this.setText('log-channel-save-label', this.t(busy ? 'logm_saving' : 'logm_save'));
        const input = byId('log-channel-input');
        if (input) input.readOnly = Boolean(busy);
    },

    /** Texto del panel "Log Channel" de la comunidad. */
    renderLogChannelStatus(logChannel) {
        const logEl = byId('chat-log-channel');
        if (!logEl) return;
        const enabled = Boolean(logChannel?.enabled && logChannel?.channel_id);
        logEl.textContent = enabled ? `${this.t('status_enabled')} (${logChannel.channel_id})` : this.t('status_disabled');
        logEl.className = enabled ? 'text-xs font-bold text-emerald-400 mt-1 truncate' : 'text-xs font-bold text-neutral-400 mt-1 truncate';
    },

    // ======================================================================
    // 🛡️ CONSOLA DE GRUPOS — interruptores de moderación (v8.2)
    // ======================================================================
    /**
     * Pinta los interruptores. loading=true: estado aún desconocido (nunca se muestra un valor inventado).
     * idle=true: no hay comunidad seleccionada ('—'). busyKey: interruptor con una petición en curso.
     */
    renderSecuritySwitches(switches, { loading = false, busyKey = null, idle = false } = {}) {
        // Compatibilidad v8.2: los interruptores heredados son ahora campos de la consola de programación.
        const legacy = CONFIG.CHAT_CONFIG?.LEGACY_SWITCHES || {};
        Object.entries(legacy).forEach(([key, field]) => {
            const known = !loading && !idle && switches && Object.prototype.hasOwnProperty.call(switches, key);
            this.setConfigSwitch(field, known && Boolean(switches[key]), { busy: busyKey === key, disabled: !known });
        });
    },

    /**
     * Rellena los campos con valores del servidor. Un campo con el foco no se toca: el operador
     * puede estar escribiendo mientras llega la respuesta y no se le debe pisar lo tecleado.
     */
    fillStudioForm(values) {
        const fields = this.STUDIO_FIELDS;
        Object.entries(fields).forEach(([key, id]) => {
            const el = byId(id);
            if (!el || document.activeElement === el) return;
            if (!values || !Object.prototype.hasOwnProperty.call(values, key)) return;   // campo no incluido: se respeta
            const value = values[key];
            const text = (value === null || value === undefined) ? '' : String(value);
            if (el.tagName === 'SELECT' && !Array.from(el.options).some(o => o.value === text)) {
                el.value = String(CONFIG.STUDIO?.DEFAULT_INTERVAL ?? 12);   // valor desconocido → intervalo por defecto
            } else {
                el.value = text;
            }
        });
        this.updatePromoCounter();
    },

    /** Marca (o limpia) el error de un campo del Estudio. `message` vacío limpia. */
    setFieldError(inputId, message) {
        const input = byId(inputId);
        const hint = byId(`${inputId}-error`);
        const invalid = Boolean(message);
        if (input) {
            input.classList.toggle('border-rose-500', invalid);
            input.classList.toggle('border-neutral-700', !invalid);
            input.setAttribute('aria-invalid', invalid ? 'true' : 'false');
        }
        if (hint) {
            hint.textContent = message || '';
            hint.classList.toggle('hidden', !invalid);
        }
    },

    clearStudioErrors() {
        Object.values(this.STUDIO_FIELDS).forEach(id => this.setFieldError(id, ''));
    },

    /** Indicador de guardado: kind ∈ idle | dirty | saving | saved | mismatch | invalid | error. */
    setStudioStatus(kind, text = '') {
        const el = byId('studio-save-status');
        if (!el) return;
        const known = Object.prototype.hasOwnProperty.call(STUDIO_STATUS_STYLE, kind) ? kind : 'idle';
        el.textContent = text;
        el.className = `text-[10px] font-semibold min-h-[14px] leading-tight ${STUDIO_STATUS_STYLE[known]}`;
        el.setAttribute('data-status', known);
    },

    /** Deshabilita el botón mientras se guarda (evita dobles envíos). */
    setStudioBusy(busy) {
        const btn = byId('studio-save-btn');
        if (!btn) return;
        btn.disabled = Boolean(busy);
        btn.classList.toggle('opacity-60', Boolean(busy));
        btn.classList.toggle('cursor-wait', Boolean(busy));
    },

    // ======================================================================
    // 🔐 SESIÓN
    // ======================================================================
    /** Aviso a pantalla completa cuando el initData de Telegram ya no es válido (no hay forma de renovarlo desde dentro). */
    showSessionExpired(show) {
        this.setVisible('session-expired', Boolean(show));
    },

    /**
     * Vacía todo lo que se pintó con datos de un operador. Se llama al cerrar sesión y al detectar que la
     * identidad cambió: ningún dato de la sesión anterior puede quedar en el DOM, ni siquiera oculto.
     */
    resetSessionView() {
        ['channels-list', 'groups-list', 'watchdog-list', 'chat-top-users-list', 'chat-admin-stats-list',
         'an-leaderboard', 'an-heatmap', 'an-breakdown-bar', 'an-breakdown-legend', 'an-live-feed',
         'an-spark-messages', 'an-spark-active', 'an-spark-growth'].forEach(id => {
            const el = byId(id);
            if (el) el.innerHTML = '';
        });

        ['channel-owner-select', 'group-owner-select', 'analytics-chat-select'].forEach(id => {
            const el = byId(id);
            if (el) {
                el.innerHTML = '<option value=""></option>';
                el.value = '';
            }
        });

        this.renderStats({});
        this.renderAffiliateLink(null);
        // Estos dos elementos llevan data-i18n: se restauran a su texto por defecto traducido.
        const planEl = byId('chat-plan-status');
        if (planEl) {
            planEl.textContent = this.t('tariff_empty');
            planEl.className = 'text-xs font-bold text-rose-400 mt-1';
        }
        const logEl = byId('chat-log-channel');
        if (logEl) {
            logEl.textContent = this.t('status_disabled');
            logEl.className = 'text-xs font-bold text-neutral-400 mt-1 truncate';
        }
        this.fillStudioForm({
            broadcast_target: '',
            broadcast_interval: CONFIG.STUDIO?.DEFAULT_INTERVAL ?? 12,
            promo_text: ''
        });
        this.resetPlanForm();
        this.updatePromoCounter();
        this.setBroadcastStatus(null);
        this.closeBroadcastPreview();
        this.closeLogChannelView();
        Object.keys(this.CONFIG_LAYOUT).forEach(section => {
            Object.entries(this.CONFIG_LAYOUT[section]).forEach(([field, kind]) => {
                if (kind === 'switch') this.setConfigSwitch(field, false, { disabled: true });
                else this.setFieldError(`cfg-${field}`, '');
            });
        });
        this.setConsoleState('idle');
        this.clearStudioErrors();
        this.setStudioStatus('idle');
        this.setText('channel-id-display', 'ID: —');
        this.renderChannelPlansState('idle');
        this.closePlanPreview();
        this.setRadarTarget('');
        this.resetAnalyticsView();
        this.renderVoiceCard();
    },

    // ======================================================================
    // 🔴 ACTIVIDAD EN VIVO (feed)
    // ======================================================================
    feedItemText(item) {
        const vars = { ...(item.vars || {}) };
        if (vars.kind !== undefined) vars.kind = this.kindLabel(vars.kind);
        return this.tf(item.key, vars);
    },

    buildFeedRow(item) {
        const row = document.createElement('div');
        row.className = 'flex items-center gap-2 py-1.5 border-b border-neutral-800/60 text-[11px]';

        const icon = document.createElement('span');
        icon.className = 'shrink-0 w-5 text-center';
        icon.textContent = item.icon || '•';

        const text = document.createElement('span');
        text.className = 'flex-1 min-w-0 truncate text-theme-main';
        text.textContent = this.feedItemText(item);   // textContent: los nombres de usuario nunca se interpretan como HTML

        const time = document.createElement('span');
        time.className = 'shrink-0 text-[9px] text-neutral-500 font-mono';
        time.textContent = new Date(item.ts).toLocaleTimeString(state.currentLang === 'es' ? 'es-CO' : 'en-US', { hour12: false });

        row.appendChild(icon);
        row.appendChild(text);
        row.appendChild(time);
        return row;
    },

    /** item: { icon, key, vars } — se localiza al pintar, así sobrevive al cambio de idioma. */
    pushFeedItem(item) {
        const entry = { ...item, ts: item.ts || Date.now() };
        const max = CONFIG.ANALYTICS?.FEED_MAX_ITEMS || 30;
        state.liveFeed.unshift(entry);
        if (state.liveFeed.length > max) state.liveFeed.length = max;

        const list = byId('an-live-feed');
        if (!list) return;
        byId('an-feed-empty')?.classList.add('hidden');
        list.prepend(this.buildFeedRow(entry));
        while (list.children.length > max) list.lastElementChild.remove();
    },

    /** Repinta el feed completo (cambio de idioma). El marcador "esperando…" vive fuera de la lista. */
    renderFeed() {
        const list = byId('an-live-feed');
        if (!list) return;
        list.innerHTML = '';
        state.liveFeed.forEach(entry => list.appendChild(this.buildFeedRow(entry)));
        byId('an-feed-empty')?.classList.toggle('hidden', state.liveFeed.length > 0);
    },

    clearFeed() {
        state.liveFeed = [];
        this.renderFeed();
    },

    // ======================================================================
    // 🔔 TOASTS FLOTANTES
    // ======================================================================
    _toasts: new Map(),      // key → { el, timer }
    _msgBuf: [],
    _msgTimer: null,

    getToastHost() {
        let host = byId('toast-stack');
        if (!host) {
            host = document.createElement('div');
            host.id = 'toast-stack';
            host.className = 'fixed inset-x-0 z-[300] flex flex-col items-center gap-2 px-4 pointer-events-none';
            host.style.top = 'calc(env(safe-area-inset-top, 0px) + 12px)';
            document.body.appendChild(host);
        }
        return host;
    },

    /**
     * Aviso flotante. Con `key` repetida se actualiza el aviso existente en lugar de apilar otro.
     * Devuelve el elemento (o null si las alertas están silenciadas y no se fuerza).
     */
    showToast({ key = null, icon = '🔔', title = '', body = '', tone = 'info', ttl = 4500, force = false } = {}) {
        if (!state.liveToastsEnabled && !force) return null;
        this.ensureLiveStyles();
        const host = this.getToastHost();

        let entry = key ? this._toasts.get(key) : null;
        if (entry) {
            clearTimeout(entry.timer);
            this._fillToast(entry.el, { icon, title, body });
        } else {
            const el = document.createElement('div');
            el.className = `bk-toast pointer-events-auto w-full max-w-sm rounded-2xl border px-3.5 py-2.5 flex items-start gap-2.5 shadow-2xl backdrop-blur-md cursor-pointer ${TOAST_TONES[tone] || TOAST_TONES.info}`;
            el.setAttribute('role', 'status');
            el.addEventListener('click', () => this.dismissToast(key || el));
            this._fillToast(el, { icon, title, body });
            host.appendChild(el);
            entry = { el, timer: null };
            this._toasts.set(key || el, entry);

            const max = CONFIG.ANALYTICS?.TOAST_MAX_VISIBLE || 3;
            while (this._toasts.size > max) {
                this.dismissToast(this._toasts.keys().next().value);
            }
        }

        entry.timer = setTimeout(() => this.dismissToast(key || entry.el), ttl);
        return entry.el;
    },

    _fillToast(el, { icon, title, body }) {
        el.textContent = '';
        const iconEl = document.createElement('span');
        iconEl.className = 'text-lg leading-none shrink-0 mt-0.5';
        iconEl.textContent = icon;

        const box = document.createElement('div');
        box.className = 'min-w-0 flex-1';
        const titleEl = document.createElement('p');
        titleEl.className = 'text-xs font-extrabold text-white break-words';
        titleEl.textContent = title;
        box.appendChild(titleEl);
        if (body) {
            const bodyEl = document.createElement('p');
            bodyEl.className = 'text-[11px] text-neutral-300 leading-snug break-words';
            bodyEl.textContent = body;
            box.appendChild(bodyEl);
        }

        el.appendChild(iconEl);
        el.appendChild(box);
    },

    dismissToast(keyOrEl) {
        const entry = this._toasts.get(keyOrEl);
        if (!entry) return;
        clearTimeout(entry.timer);
        this._toasts.delete(keyOrEl);
        entry.el.classList.add('bk-toast-out');
        setTimeout(() => entry.el.remove(), 200);
    },

    clearToasts() {
        [...this._toasts.keys()].forEach(key => this.dismissToast(key));
        clearTimeout(this._msgTimer);
        this._msgTimer = null;
        this._msgBuf = [];
    },

    /** Ascenso de nivel de gamificación (evento level_up). */
    notifyLevelUp({ name, level, userId }) {
        this.showToast({
            key: `level-${userId ?? name}`,
            icon: '🏆',
            tone: 'level',
            ttl: 6500,
            title: this.t('toast_level_up_title'),
            body: this.tf('toast_level_up_body', { name, level })
        });
    },

    /**
     * Mensajes en vivo: se agrupan en ventanas de unos segundos para no inundar la pantalla
     * en comunidades activas (un aviso por ventana, que se actualiza en lugar de apilarse).
     */
    notifyLiveMessage({ name, kind }) {
        if (!state.liveToastsEnabled || document.hidden) return;
        this._msgBuf.push({ name, kind });
        if (this._msgTimer) return;
        this._msgTimer = setTimeout(() => this.flushMessageToast(), CONFIG.ANALYTICS?.MESSAGE_TOAST_WINDOW_MS || 2500);
    },

    flushMessageToast() {
        const buffer = this._msgBuf;
        this._msgBuf = [];
        this._msgTimer = null;
        if (!buffer.length) return;

        if (buffer.length === 1) {
            const only = buffer[0];
            this.showToast({
                key: 'live-messages',
                icon: (KIND_META[only.kind] || KIND_META.other).icon,
                tone: 'info',
                ttl: 3500,
                title: this.t('toast_msg_title_one'),
                body: this.tf('feed_message', { name: only.name, kind: this.kindLabel(only.kind) })
            });
            return;
        }

        const names = [...new Set(buffer.map(item => item.name))];
        const shown = names.slice(0, 3).join(', ');
        this.showToast({
            key: 'live-messages',
            icon: '💬',
            tone: 'info',
            ttl: 3500,
            title: this.tf('toast_msg_title_many', { n: buffer.length }),
            body: names.length > 3 ? `${shown} +${names.length - 3}` : shown
        });
    },

    notifyVoiceCall(started) {
        this.showToast({
            key: 'voice-call',
            icon: started ? '🎙️' : '🔇',
            tone: started ? 'success' : 'warn',
            ttl: 4500,
            title: this.t(started ? 'toast_voice_started' : 'toast_voice_ended')
        });
    },

    notifyStarsPayment({ name, stars }) {
        this.showToast({
            key: `payment-${Date.now()}`,
            icon: '⭐',
            tone: 'success',
            ttl: 6000,
            title: this.t('toast_payment_title'),
            body: this.tf('toast_payment_body', { stars, name })
        });
    }
};