/* ==========================================================================
   THE BUNKER — COMMAND OS
   ui.js — Renderizado del DOM, Componentes Visuales, Analítica en Vivo, Estudio de Canales y Toasts
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
    // 🎬 ESTUDIO DE CANALES — formulario reactivo
    // ======================================================================
    STUDIO_FIELDS: {
        target_link:   'studio-target-link',
        stars_price:   'studio-stars-price',
        duration_days: 'studio-duration-days'
    },

    /** Valores crudos (texto) de los tres campos del Estudio. */
    readStudioForm() {
        const read = (id) => byId(id)?.value ?? '';
        return {
            target_link: read(this.STUDIO_FIELDS.target_link),
            stars_price: read(this.STUDIO_FIELDS.stars_price),
            duration_days: read(this.STUDIO_FIELDS.duration_days)
        };
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
            const value = values?.[key];
            el.value = (value === null || value === undefined) ? '' : String(value);
        });
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
            target_link: '',
            stars_price: CONFIG.STUDIO?.DEFAULT_PRICE ?? 150,
            duration_days: CONFIG.STUDIO?.DEFAULT_DAYS ?? 30
        });
        this.clearStudioErrors();
        this.setStudioStatus('idle');
        this.setText('channel-id-display', 'ID: —');
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
        titleEl.className = 'text-xs font-extrabold text-white truncate';
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