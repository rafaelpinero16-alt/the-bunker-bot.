/* ==========================================================================
   THE BUNKER — COMMAND OS
   app.js — Lógica central, i18n, telemetría real y pasarelas de pago
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

const CONFIG = {
    BOT_USERNAME: 'thebunkerapp_bot',
    API_BASE: 'https://alpha-bunker-backend-production.up.railway.app/api',
    TON_MANIFEST: 'https://alpha-bunker-backend-production.up.railway.app/tonconnect-manifest.json',
    TON_WALLET: 'UQAAnX4bGBzI0ujk35-XChap_wZ7x67NeJ85C_M1YIvLbYUF',
    PRICES: {
        pro:   { stars: 500, usd: 5, ton: '1000000000' },
        ultra: { stars: 800, usd: 8, ton: '1600000000' }
    }
};

const translations = {
    es: {
        plans_title: "Membresías de Comunidad",
        basic_badge: "ACCESO GRATUITO",
        basic_title: "Plan BÁSICO",
        basic_desc: "Comandos de moderación limitados. Sin automatización de radar ni soporte prioritario.",
        btn_free_active: "Activo por defecto",
        pro_badge: "PLAN MENSUAL",
        pro_title: "Plan PRO",
        pro_desc: "Automatización total, moderación de voz y comandos ilimitados para tu comunidad.",
        ultra_badge: "ACCESO ILIMITADO",
        ultra_title: "ULTRA PRO",
        ultra_desc: "Radar avanzado anti-fantasmas, prioridad máxima en servidores y soporte directo.",
        tactical_gateways: "PASARELAS TÁCTICAS",
        loading_user: "Cargando...",
        verifying: "@verificando",
        active_context: "Entorno Activo:",
        stat_subs: "Suscriptores VIP",
        stat_revenue: "Ingresos Stars",
        live_sync: "Sincronización en vivo",
        stat_source: "Datos reales del bot",
        dash_telemetry: "Telemetría del Búnker",
        online_tag: "En Línea 🟢",
        stat_verified: "Verificados",
        stat_expelled: "Expulsados",
        stat_purges: "Purgas",
        perimeter_title: "Perímetro Operativo",
        perimeter_sub: "Supervisión 24/7",
        captcha_label: "Aduana Captcha:",
        autolower_label: "Radar AutoLower:",
        shield_label: "Escudo Antinota:",
        broadcast_label: "Difusión Recurrente:",
        linked_channels: "Canales Conectados",
        linked_groups: "Comunidades Conectadas",
        sync: "Sincronizar",
        configure: "Configurar",
        license_active: "Activa 🟢",
        license_renew: "Renovar",
        no_channels: "Sin canales vinculados aún",
        no_groups: "Sin comunidades vinculadas aún",
        no_subs: "Sin suscriptores registrados",
        no_channels_select: "No hay canales vinculados",
        no_groups_select: "No hay comunidades vinculadas",
        aff_title: "Red de Afiliados & Comisiones",
        aff_desc: "Comparte tu enlace único de promotor. Cada vez que una comunidad adquiera un plan PRO o ULTRA a través de tu recomendación, recibes comisiones instantáneas en Telegram Stars directo a tu balance.",
        aff_invited: "Comunidades",
        aff_earned: "Comisión Stars",
        aff_balance: "Balance Actual",
        nav_dash: "Control",
        nav_channels: "Canales",
        nav_groups: "Grupos",
        nav_watchdog: "Auditor",
        nav_plans: "Membresías",
        nav_aff: "Afiliados"
    },
    en: {
        plans_title: "Community Memberships",
        basic_badge: "FREE TIER",
        basic_title: "BASIC Plan",
        basic_desc: "Limited moderation commands. No radar automation or priority support.",
        btn_free_active: "Active by Default",
        pro_badge: "MONTHLY PLAN",
        pro_title: "PRO Plan",
        pro_desc: "Total automation, voice moderation, and unlimited commands for your community.",
        ultra_badge: "UNLIMITED ACCESS",
        ultra_title: "ULTRA PRO",
        ultra_desc: "Advanced anti-ghost radar, max server priority, and direct support.",
        tactical_gateways: "TACTICAL GATEWAYS",
        loading_user: "Loading...",
        verifying: "@verifying",
        active_context: "Active Context:",
        stat_subs: "VIP Subscribers",
        stat_revenue: "Stars Revenue",
        live_sync: "Live sync",
        stat_source: "Real bot data",
        dash_telemetry: "Bunker Telemetry",
        online_tag: "Online 🟢",
        stat_verified: "Verified",
        stat_expelled: "Expelled",
        stat_purges: "Purges",
        perimeter_title: "Operational Perimeter",
        perimeter_sub: "24/7 Monitoring",
        captcha_label: "Captcha Customs:",
        autolower_label: "AutoLower Radar:",
        shield_label: "Screenshot Shield:",
        broadcast_label: "Recurring Broadcast:",
        linked_channels: "Connected Channels",
        linked_groups: "Connected Communities",
        sync: "Sync",
        configure: "Configure",
        license_active: "Active 🟢",
        license_renew: "Renew",
        no_channels: "No channels linked yet",
        no_groups: "No communities linked yet",
        no_subs: "No subscribers registered",
        no_channels_select: "No channels linked",
        no_groups_select: "No communities linked",
        aff_title: "Affiliate & Commission Network",
        aff_desc: "Share your promoter link. Whenever a community unlocks a PRO or ULTRA plan through your recommendation, receive direct Telegram Stars commissions.",
        aff_invited: "Communities",
        aff_earned: "Stars Earned",
        aff_balance: "Current Balance",
        nav_dash: "Control",
        nav_channels: "Channels",
        nav_groups: "Groups",
        nav_watchdog: "Auditor",
        nav_plans: "Memberships",
        nav_aff: "Affiliates"
    }
};

const app = {
    tonConnectUI: null,
    currentLang: 'es',
    selectedPlan: 'pro',
    currentTab: 'dashboard',
    activeContext: 'global',
    state: {
        channels: [],
        groups: [],
        subscribers: []
    },

    init() {
        const tg = window.Telegram?.WebApp;
        if (tg) {
            tg.expand();
            tg.ready();
            if (tg.setHeaderColor) { try { tg.setHeaderColor('#030407'); } catch (e) {} }
            if (tg.setBackgroundColor) { try { tg.setBackgroundColor('#030407'); } catch (e) {} }
        }
        this.loadTelegramUser();
        this.initTonConnect();
        this.updateTranslations();
        this.loadAffiliateLink();

        this.loadStats();
        this.loadChannels();
        this.loadGroups();
        this.loadSubscribers();
        this.loadAffiliateStats();
    },

    t(key) {
        return (translations[this.currentLang] && translations[this.currentLang][key]) || key;
    },

    toggleDrawer(show) {
        const drawer = document.getElementById('cyber-drawer');
        if (!drawer) return;
        drawer.classList.toggle('hidden', !show);
        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
        }
    },

    switchTab(tabId) {
        this.currentTab = tabId;
        document.querySelectorAll('.tab-view').forEach(el => el.classList.add('hidden'));

        const targetView = document.getElementById(`view-${tabId}`);
        if (targetView) targetView.classList.remove('hidden');

        document.querySelectorAll('nav button[id^="nav-"]').forEach(btn => {
            btn.classList.remove('text-[#00f3ff]');
            btn.classList.add('text-neutral-400');
        });
        const activeNavBtn = document.getElementById(`nav-${tabId}`);
        if (activeNavBtn) {
            activeNavBtn.classList.remove('text-neutral-400');
            activeNavBtn.classList.add('text-[#00f3ff]');
        }

        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
        }
    },

    switchContext(val) {
        this.activeContext = val;
        this.loadStats();
        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.selectionChanged();
        }
    },

    toggleLanguage() {
        this.currentLang = this.currentLang === 'es' ? 'en' : 'es';
        const indicator = document.getElementById('lang-indicator');
        if (indicator) indicator.innerText = this.currentLang.toUpperCase();

        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
        }
        this.updateTranslations();
        this.renderChatList('channels-list', this.state.channels, this.t('no_channels'));
        this.renderChatList('groups-list', this.state.groups, this.t('no_groups'));
        this.renderSubscriberList(this.state.subscribers);
    },

    updateTranslations() {
        const dict = translations[this.currentLang];
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (dict[key]) el.innerText = dict[key];
        });
    },

    loadTelegramUser() {
        const user = window.Telegram?.WebApp?.initDataUnsafe?.user;
        const nameEl = document.getElementById('user-name');
        const handleEl = document.getElementById('user-handle');
        const idEl = document.getElementById('user-id-display');
        const imgEl = document.getElementById('avatar-img');
        const initialsEl = document.getElementById('avatar-initials');

        if (user) {
            if (nameEl) nameEl.innerText = `${user.first_name || ''} ${user.last_name || ''}`.trim() || 'Usuario';
            if (handleEl) handleEl.innerText = user.username ? `@${user.username}` : `ID: ${user.id}`;
            if (idEl) idEl.innerText = `ID: ${user.id}`;

            const initials = ((user.first_name || '?').charAt(0) + (user.last_name ? user.last_name.charAt(0) : '')).toUpperCase();

            if (user.photo_url && imgEl && initialsEl) {
                imgEl.src = user.photo_url;
                imgEl.classList.remove('hidden');
                initialsEl.classList.add('hidden');
            } else if (initialsEl) {
                initialsEl.innerText = initials;
            }
        } else {
            if (nameEl) nameEl.innerText = this.t('loading_user');
            if (handleEl) handleEl.innerText = this.t('verifying');
            if (idEl) idEl.innerText = 'ID: —';
            if (initialsEl) initialsEl.innerText = '?';
        }
    },

    loadAffiliateLink() {
        const user = window.Telegram?.WebApp?.initDataUnsafe?.user;
        const userId = user ? user.id : '000';
        const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}`;
        const el = document.getElementById('affiliate-link-text');
        if (el) el.innerText = link;
    },

    copyAffiliateLink() {
        const user = window.Telegram?.WebApp?.initDataUnsafe?.user;
        const userId = user ? user.id : '000';
        const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}`;
        this.copyText(link);
    },

    async apiGet(path) {
        const initData = window.Telegram?.WebApp?.initData || '';
        try {
            const res = await fetch(`${CONFIG.API_BASE}${path}`, {
                headers: { 'X-Telegram-Init-Data': initData }
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (err) {
            console.warn(`[Bunker API] ${path} offline, usando fallback local:`, err.message);
            return null; // Fallback graceful sin datos falsos
        }
    },

    setStat(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerText = (value === null || value === undefined || value === '') ? '0' : value;
    },

    async loadStats() {
        const data = await this.apiGet(`/stats?context=${this.activeContext}`);
        this.setStat('stat-subs-count', data?.subscribers ?? 0);
        this.setStat('stat-revenue-count', data?.revenue_stars != null ? `${data.revenue_stars} ⭐` : '0 ⭐');
        this.setStat('stat-verified', data?.verified ?? 0);
        this.setStat('stat-expelled', data?.expelled ?? 0);
        this.setStat('stat-purges', data?.purges ?? 0);

        const perimeter = data?.perimeter || {};
        this.setStat('telemetry-captcha', perimeter.captcha || (this.currentLang === 'es' ? 'Desactivada 🔴' : 'Disabled 🔴'));
        this.setStat('telemetry-autolower', perimeter.autolower || (this.currentLang === 'es' ? 'Desactivado 🔴' : 'Disabled 🔴'));
        this.setStat('telemetry-shield', perimeter.shield || (this.currentLang === 'es' ? 'Desactivado 🔴' : 'Disabled 🔴'));
        this.setStat('telemetry-broadcast', perimeter.broadcast || (this.currentLang === 'es' ? 'Desactivada 🔴' : 'Disabled 🔴'));

        this.setSwitch('switch-captcha', perimeter.captcha_active);
        this.setSwitch('switch-autolower', perimeter.autolower_active);
        this.setSwitch('switch-shield', perimeter.shield_active);
        this.setSwitch('switch-linklock', perimeter.linklock_active);
    },

    setSwitch(id, active) {
        const el = document.getElementById(id);
        if (!el) return;
        if (active === true) {
            el.className = 'text-emerald-400 font-bold';
            el.innerText = this.currentLang === 'es' ? 'ACTIVO 🟢' : 'ACTIVE 🟢';
        } else if (active === false) {
            el.className = 'text-rose-400 font-bold';
            el.innerText = this.currentLang === 'es' ? 'BLOQUEADO 🔴' : 'BLOCKED 🔴';
        } else {
            el.className = 'text-neutral-500 font-bold';
            el.innerText = this.currentLang === 'es' ? 'INACTIVO 🔴' : 'INACTIVE 🔴';
        }
    },

    async loadChannels() {
        const data = await this.apiGet('/channels');
        this.state.channels = (data && data.channels) || [];
        this.renderChatList('channels-list', this.state.channels, this.t('no_channels'));
        this.populateSelect('channel-owner-select', this.state.channels, this.t('no_channels_select'));
    },

    async loadGroups() {
        const data = await this.apiGet('/groups');
        this.state.groups = (data && data.groups) || [];
        this.renderChatList('groups-list', this.state.groups, this.t('no_groups'));
        this.populateSelect('group-owner-select', this.state.groups, this.t('no_groups_select'));
    },

    async loadSubscribers() {
        const data = await this.apiGet('/subscribers');
        this.state.subscribers = (data && data.subscribers) || [];
        this.renderSubscriberList(this.state.subscribers);
    },

    async loadAffiliateStats() {
        const data = await this.apiGet('/affiliates/me');
        this.setStat('aff-invited-count', data ? data.invited_communities : 0);
        this.setStat('aff-earned-count', data ? `${data.earned_stars} ⭐` : '0 ⭐');
        this.setStat('aff-balance-count', data ? `${data.balance} ⭐` : '0 ⭐');
    },

    populateSelect(id, list, emptyLabel) {
        const sel = document.getElementById(id);
        if (!sel) return;
        if (!list || list.length === 0) {
            sel.innerHTML = `<option value="">${emptyLabel}</option>`;
            sel.disabled = true;
            return;
        }
        sel.disabled = false;
        sel.innerHTML = list.map(c => `<option value="${c.id}">${c.type === 'channel' ? '📢' : '🛡️'} ${c.title} (ID: ${c.id})</option>`).join('');
    },

    generateSparkline(data, color) {
        const width = 300, height = 90;
        if (!data || data.length < 2) {
            return `<svg viewBox="0 0 ${width} ${height}" class="w-full h-full">
                <text x="50%" y="50%" fill="#3a3f4b" font-size="10" text-anchor="middle" dominant-baseline="middle" font-family="monospace">${this.currentLang === 'es' ? 'SIN ACTIVIDAD' : 'NO ACTIVITY'}</text>
            </svg>`;
        }
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
                    <stop offset="0%" stop-color="${c}" stop-opacity="0.45"/>
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
        const statusHtml = licenseActive
            ? `<span class="text-emerald-400 font-bold text-[10px] shrink-0">${this.t('license_active')}</span>`
            : `<button onclick="app.renewLicense('${chat.id}')" class="bg-rose-500/20 text-rose-400 border border-rose-500/50 px-2.5 py-1 rounded-lg text-[10px] font-bold hover:bg-rose-500/30 active:scale-95 transition shrink-0">${this.t('license_renew')}</button>`;

        const hasDelta = chat.joined != null || chat.left != null;
        const deltaHtml = hasDelta
            ? `<span class="text-emerald-400">+${chat.joined ?? 0}</span> <span class="text-rose-400 ml-1.5">-${chat.left ?? 0}</span>`
            : `<span class="text-neutral-600">—</span>`;

        const avatarHtml = chat.avatar_url
            ? `<img src="${chat.avatar_url}" class="w-full h-full object-cover" alt="">`
            : `<i class="fa-solid ${isChannel ? 'fa-tower-broadcast' : 'fa-shield-halved'} text-[#00f3ff] text-xs"></i>`;

        return `
        <div class="glass-panel p-3.5 space-y-2.5 gpu-accelerated" data-chat-id="${chat.id}">
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                    <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-[#00f3ff]/25 to-[#ff00ff]/25 flex items-center justify-center shrink-0 overflow-hidden border border-white/10">
                        ${avatarHtml}
                    </div>
                    <div class="min-w-0">
                        <p class="text-xs font-bold text-white truncate max-w-[130px]">${chat.title}</p>
                        <p class="text-[9px] text-neutral-400 font-mono flex items-center gap-1"><i class="fa-solid fa-user text-[8px]"></i> ${chat.members ?? '—'}</p>
                    </div>
                </div>
                ${statusHtml}
            </div>
            <div class="h-16 w-full">${this.generateSparkline(chat.activity, isChannel ? '#00f3ff' : '#39ff88')}</div>
            <div class="flex items-center justify-between border-t border-neutral-800 pt-2 font-mono">
                <span class="text-[9px]">${deltaHtml}</span>
                <button onclick="app.configureChat('${chat.id}')" class="text-[9px] text-[#00f3ff] font-bold uppercase flex items-center gap-1 hover:text-white transition">
                    <i class="fa-solid fa-gear"></i> ${this.t('configure')}
                </button>
            </div>
            <p class="text-center text-[7px] text-neutral-700 font-mono tracking-widest uppercase pt-1.5 border-t border-neutral-900">Bunker Command OS © 2026 — Cloud Media Management</p>
        </div>`;
    },

    emptyStateTemplate(message) {
        return `
        <div class="glass-panel p-8 flex flex-col items-center justify-center text-center space-y-2 opacity-70">
            <i class="fa-solid fa-satellite-dish text-2xl text-neutral-600"></i>
            <p class="text-[11px] text-neutral-500 font-mono uppercase tracking-widest">${message}</p>
        </div>`;
    },

    renderChatList(containerId, list, emptyMsg) {
        const el = document.getElementById(containerId);
        if (!el) return;
        if (!list || list.length === 0) {
            el.innerHTML = this.emptyStateTemplate(emptyMsg);
            return;
        }
        el.innerHTML = list.map(c => this.chatCardTemplate(c)).join('');
    },

    subscriberTemplate(s) {
        const daysLeft = s.days_left ?? null;
        const statusColor = daysLeft === null ? 'neutral-500' : (daysLeft > 5 ? 'emerald-400' : 'amber-400');
        const daysLabel = daysLeft === null ? '—' : `${daysLeft} ${this.currentLang === 'es' ? 'días restantes' : 'days left'}`;
        return `
        <div class="bg-black/60 p-3.5 rounded-xl border border-neutral-800 flex items-center justify-between">
            <div>
                <p class="text-white font-bold">@${s.username || s.user_id || '—'}</p>
                <p class="text-[10px] text-neutral-400">${s.plan_name || '—'} (${s.price != null ? s.price + ' ⭐' : '—'})</p>
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
            el.innerHTML = this.emptyStateTemplate(this.t('no_subs'));
            return;
        }
        el.innerHTML = list.map(s => this.subscriberTemplate(s)).join('');
    },

    renewLicense(chatId) {
        this.switchTab('plans');
        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.impactOccurred('medium');
        }
    },

    configureChat(chatId) {
        const chat = [...this.state.channels, ...this.state.groups].find(c => String(c.id) === String(chatId));
        if (!chat) return;
        const isChannel = chat.type === 'channel';
        this.switchTab(isChannel ? 'channels' : 'groups');
        const sel = document.getElementById(isChannel ? 'channel-owner-select' : 'group-owner-select');
        if (sel) sel.value = chatId;
    },

    openChannelStudio() {
        const select = document.getElementById('channel-owner-select');
        const channelId = select ? select.value : '';
        const targetLink = document.getElementById('studio-target-link')?.value || '';
        const price = document.getElementById('studio-stars-price')?.value || '';
        const days = document.getElementById('studio-duration-days')?.value || '';

        if (!channelId) {
            alert(this.currentLang === 'es' ? '⚠️ Selecciona un canal vinculado primero.' : '⚠️ Select a linked channel first.');
            return;
        }

        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
        }

        const msg = this.currentLang === 'es'
            ? `📡 Sincronización del Estudio:\n\n• Canal ID: ${channelId}\n• Tarifa: ${price} Stars (XTR)\n• Duración: ${days} días\n• Destino VIP: ${targetLink}`
            : `📡 Studio Synchronized:\n\n• Channel ID: ${channelId}\n• Rate: ${price} Stars (XTR)\n• Duration: ${days} days\n• VIP Target: ${targetLink}`;

        alert(msg);
    },

    async fetchTonBalance(address) {
        try {
            const res = await fetch(`https://toncenter.com/api/v2/getAddressBalance?address=${address}`);
            const data = await res.json();
            if (data.ok) return (parseInt(data.result) / 1e9).toFixed(2);
        } catch (e) { console.error('Error obteniendo balance:', e); }
        return '0.00';
    },

    async updateWalletUI(account) {
        const btnText = document.getElementById('wallet-btn-text');
        const statusDot = document.getElementById('wallet-status-dot');
        if (!btnText || !statusDot) return;

        if (account) {
            const addr = account.address;
            btnText.innerText = this.currentLang === 'es' ? 'Cargando...' : 'Loading...';
            statusDot.className = 'w-2 h-2 rounded-full bg-emerald-400 mr-1.5 shadow-[0_0_8px_rgba(52,211,153,0.8)] animate-pulse';

            const balance = await this.fetchTonBalance(addr);
            const shortAddr = addr.slice(0, 4) + '...' + addr.slice(-4);
            btnText.innerText = `${shortAddr} | ${balance} TON`;
            statusDot.classList.remove('animate-pulse');
        } else {
            btnText.innerText = 'TON Wallet';
            statusDot.className = 'w-2 h-2 rounded-full bg-amber-500 mr-1.5 animate-pulse';
        }
    },

    async initTonConnect() {
        const TonConnectClass = window.TON_CONNECT_UI?.TonConnectUI || window.TonConnectUI;
        if (!this.tonConnectUI && TonConnectClass) {
            try {
                this.tonConnectUI = new TonConnectClass({
                    manifestUrl: CONFIG.TON_MANIFEST,
                    uiPreferences: { theme: 'DARK' }
                });

                await this.tonConnectUI.connectionRestored;

                if (this.tonConnectUI.connected && this.tonConnectUI.account) {
                    this.updateWalletUI(this.tonConnectUI.account);
                } else {
                    this.updateWalletUI(null);
                }

                this.tonConnectUI.onStatusChange(async (wallet) => {
                    this.updateWalletUI(wallet?.account);
                });
            } catch (err) {
                console.warn('[TON Connect] Esperando inicialización:', err);
            }
        }
    },

    async connectWallet() {
        try {
            if (!this.tonConnectUI) await this.initTonConnect();
            if (!this.tonConnectUI) { alert('⚠️ TON Connect no disponible.'); return; }

            if (this.tonConnectUI.connected) {
                if (confirm(this.currentLang === 'es' ? '¿Deseas desconectar tu TON Wallet?' : 'Disconnect TON Wallet?')) {
                    await this.tonConnectUI.disconnect();
                    this.updateWalletUI(null);
                }
            } else {
                await this.tonConnectUI.openModal();
            }
        } catch (e) {
            console.error('Error conectando wallet:', e);
        }
    },

    async executeTonPayment(plan) {
        const nanoAmount = CONFIG.PRICES[plan]?.ton || '1000000000';
        const tx = {
            validUntil: Math.floor(Date.now() / 1000) + 300,
            messages: [{ address: CONFIG.TON_WALLET, amount: nanoAmount }]
        };

        try {
            const result = await this.tonConnectUI.sendTransaction(tx);
            if (result) {
                alert(this.currentLang === 'es' ? '✅ Transacción exitosa. ¡Activación procesada!' : '✅ Transaction successful.');
                this.closeCheckout();
            }
        } catch (err) {
            console.warn('Transacción fallida/cancelada:', err);
        }
    },

    pagarPlan(plan, method) {
        this.selectedPlan = plan;
        const priceInfo = CONFIG.PRICES[plan];
        if (!priceInfo) return;

        if (method === 'stars') {
            const param = plan === 'pro' ? 'sub_pro' : 'sub_ultra';
            if (window.Telegram?.WebApp) {
                window.Telegram.WebApp.openTelegramLink(`https://t.me/${CONFIG.BOT_USERNAME}?start=${param}`);
                setTimeout(() => window.Telegram.WebApp.close(), 300);
            }
        } else if (method === 'paypal') {
            window.open(`https://paypal.me/Felipecosmic/${priceInfo.usd}`, '_blank');
        } else if (method === 'ton') {
            if (!this.tonConnectUI || !this.tonConnectUI.connected) {
                alert(this.currentLang === 'es' ? '⚠️ Conecta tu TON Wallet primero.' : '⚠️ Connect your TON Wallet first.');
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
        const priceUsd = `${CONFIG.PRICES[this.selectedPlan]?.usd || 5}.00`;
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
            alert(this.currentLang === 'es' ? '¡Copiado al portapapeles! 📋' : 'Copied to clipboard! 📋');
        }).catch(() => {
            alert(this.currentLang === 'es' ? '⚠️ No se pudo copiar.' : '⚠️ Could not copy.');
        });
    }
};

window.app = app;
document.addEventListener('DOMContentLoaded', () => app.init());