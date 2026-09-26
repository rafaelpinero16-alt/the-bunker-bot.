/* ==========================================================================
   THE BUNKER — COMMAND OS
   app.js — Lógica central, Draggable Button, Theme Engine, i18n y Telemetría
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
        btn_logout: "Cerrar Sesión",
        no_channels: "Sin canales vinculados aún",
        no_groups: "Sin comunidades vinculadas aún",
        no_subs: "Sin suscriptores registrados",
        aff_title: "Red de Afiliados & Comisiones",
        aff_desc: "Comparte tu enlace único de promotor. Cada vez que una comunidad adquiera un plan PRO o ULTRA a través de tu recomendación, recibes comisiones instantáneas en Telegram Stars directo a tu balance.",
        nav_dash: "Telemetría & Stats",
        nav_channels: "Estudio de Canales",
        nav_groups: "Matriz de Grupos",
        nav_watchdog: "Auditor Suscripciones",
        nav_plans: "Membresías & Pasarelas",
        nav_aff: "Red de Afiliados",
        studio_badge: "ESTUDIO DE BROADCAST",
        studio_title: "Ajustes del Canal",
        btn_open_studio: "Abrir Estudio",
        channel_owner_label: "Canal Bajo tu ID de Creador:",
        vip_delivery_title: "Enlace VIP & Entrega Automática",
        vip_target_label: "Destino VIP (Canal Privado / Recurso):",
        stars_rate_label: "Tarifa en Stars (XTR):",
        duration_days_label: "Duración (Días):",
        perimeter_badge: "PERÍMETRO COMUNITARIO",
        groups_title: "Control de Grupos",
        shielded_mode: "Modo Blindado 🟢",
        group_owner_label: "Comunidad Administrada:",
        captcha_pro: "Aduana Captcha Pro",
        captcha_desc: "Desafío privado antes de ingresar",
        autolower_title: "AutoLower de Micrófonos (2%)",
        autolower_desc: "Atenuación acústica de fondo",
        shield_title: "Escudo Antinota",
        shield_desc: "Corte de pantalla compartida",
        linklock_title: "Cerradura de Enlaces Web",
        linklock_desc: "Purga inmediata de URLs no permitidas",
        watchdog_badge: "MONITOR DE MIEMBROS",
        watchdog_title: "Aviso de Suscripciones",
        live_badge: "En Vivo",
        watchdog_desc: "Supervisa en tiempo real el tiempo restante de cada miembro suscrito y las alertas de renovación antes del Auto-Kick automático.",
        profile_balance_stars: "Balance Stars:",
        profile_discount: "Descuento Activo:",
        profile_rank: "Rango Operativo:",
        profile_reg_date: "Fecha de Registro:",
        profile_account_status: "Estado de Cuenta:",
        btn_terminate_sessions: "Cerrar Todas las Sesiones Activas",
        bank_transfer_title: "PAGO MANUAL BANCARIO",
        theme_light: "Claro",
        theme_dark: "Oscuro",
        menu_my_profile: "My profile",
        menu_help: "Help & Support",
        menu_company_reg: "Company registration",
        help_support_chat: "Technical support chat",
        help_instruction: "Instruction & Manuals",
        help_test_regex: "Test Regex / Pattern Checker",
        tab_report_error: "Report an error",
        tab_leave_review: "Leave a review",
        click_attach: "Click to attach file",
        btn_cancel: "Cancel",
        btn_send: "Send message",
        instruction_title: "Instrucciones & Manual Oficial",
        btn_close: "Cerrar"
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
        btn_logout: "Logout",
        no_channels: "No channels linked yet",
        no_groups: "No communities linked yet",
        no_subs: "No subscribers registered",
        aff_title: "Affiliate & Commission Network",
        aff_desc: "Share your promoter link. Whenever a community unlocks a PRO or ULTRA plan through your recommendation, receive direct Telegram Stars commissions.",
        nav_dash: "Telemetry & Stats",
        nav_channels: "Channel Studio",
        nav_groups: "Group Matrix",
        nav_watchdog: "Sub Watchdog",
        nav_plans: "Plans & Gateways",
        nav_aff: "Affiliate Network",
        studio_badge: "BROADCAST STUDIO",
        studio_title: "Channel Settings",
        btn_open_studio: "Open Studio",
        channel_owner_label: "Channel under your Creator ID:",
        vip_delivery_title: "VIP Link & Automatic Delivery",
        vip_target_label: "VIP Target (Private Channel / Resource):",
        stars_rate_label: "Rate in Stars (XTR):",
        duration_days_label: "Duration (Days):",
        perimeter_badge: "COMMUNITY PERIMETER",
        groups_title: "Group Control",
        shielded_mode: "Shielded Mode 🟢",
        group_owner_label: "Managed Community:",
        captcha_pro: "Captcha Customs Pro",
        captcha_desc: "Private checkpoint upon entry",
        autolower_title: "Microphone AutoLower (2%)",
        autolower_desc: "Background acoustic attenuation",
        shield_title: "Screen-Sharing Shield",
        shield_desc: "Blocks unauthorized screen share",
        linklock_title: "Web Links Lock",
        linklock_desc: "Instant purge of unapproved URLs",
        watchdog_badge: "MEMBER MONITOR",
        watchdog_title: "Subscription Watchdog",
        live_badge: "Live",
        watchdog_desc: "Real-time tracking of active members and renewal warnings before automated expulsion.",
        profile_balance_stars: "Stars Balance:",
        profile_discount: "Active Discount:",
        profile_rank: "Operational Rank:",
        profile_reg_date: "Registration Date:",
        profile_account_status: "Account Status:",
        btn_terminate_sessions: "Terminate All Active Sessions",
        bank_transfer_title: "MANUAL BANK WIRE",
        theme_light: "Light",
        theme_dark: "Dark",
        menu_my_profile: "My profile",
        menu_help: "Help & Support",
        menu_company_reg: "Company registration",
        help_support_chat: "Technical support chat",
        help_instruction: "Instruction & Manuals",
        help_test_regex: "Test Regex / Pattern Checker",
        tab_report_error: "Report an error",
        tab_leave_review: "Leave a review",
        click_attach: "Click to attach file",
        btn_cancel: "Cancel",
        btn_send: "Send message",
        instruction_title: "Instructions & Official Manual",
        btn_close: "Close"
    }
};

const app = {
    tonConnectUI: null,
    currentLang: 'es',
    currentTheme: 'dark',
    selectedPlan: 'pro',
    currentTab: 'dashboard',
    activeContext: 'global',
    securitySwitches: {
        captcha: true,
        autolower: true,
        shield: true,
        linklock: false
    },
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
        }
        this.initTheme();
        this.loadTelegramUser();
        this.initTonConnect();
        this.updateTranslations();
        this.loadAffiliateLink();
        this.initDraggableButton();

        this.loadStats();
        this.loadChannels();
        this.loadGroups();
        this.loadSubscribers();
    },

    t(key) {
        return (translations[this.currentLang] && translations[this.currentLang][key]) || key;
    },

    /* ---------------------------------------------------------------- */
    /* MOTOR DE TEMAS (LIGHT / DARK)                                    */
    /* ---------------------------------------------------------------- */
    initTheme() {
        const savedTheme = localStorage.getItem('bunker_theme') || 'dark';
        this.setTheme(savedTheme);
    },

    setTheme(theme) {
        this.currentTheme = theme;
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

        const tg = window.Telegram?.WebApp;
        if (tg) {
            const color = theme === 'light' ? '#f1f5f9' : '#030407';
            if (tg.setHeaderColor) { try { tg.setHeaderColor(color); } catch (e) {} }
            if (tg.setBackgroundColor) { try { tg.setBackgroundColor(color); } catch (e) {} }
        }
    },

    /* ---------------------------------------------------------------- */
    /* CONTROLADOR MATEMÁTICO DRAGGABLE PARA EL BOTÓN DE IDIOMA        */
    /* ---------------------------------------------------------------- */
    initDraggableButton() {
        const btn = document.getElementById('floating-lang-btn');
        if (!btn) return;

        let isDragging = false;
        let startX = 0, startY = 0;
        let btnStartX = 0, btnStartY = 0;

        const onStart = (e) => {
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
        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
        }
    },

    switchTab(tabId) {
        this.currentTab = tabId;
        document.querySelectorAll('.tab-view').forEach(el => el.classList.add('hidden'));

        const targetView = document.getElementById(`view-${tabId}`);
        if (targetView) targetView.classList.remove('hidden');

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

    toggleSecuritySwitch(key) {
        this.securitySwitches[key] = !this.securitySwitches[key];
        const el = document.getElementById(`switch-${key}`);
        if (el) {
            const active = this.securitySwitches[key];
            if (active) {
                el.className = "text-emerald-400 font-bold";
                el.innerText = this.currentLang === 'es' ? "ACTIVO 🟢" : "ACTIVE 🟢";
            } else {
                el.className = "text-rose-400 font-bold";
                el.innerText = this.currentLang === 'es' ? "BLOQUEADO 🔴" : "BLOCKED 🔴";
            }
        }
        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.impactOccurred('medium');
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

    /* ---------------------------------------------------------------- */
    /* CONTROLADORES DE MODALES NUEVOS (ChatKeeper Style)               */
    /* ---------------------------------------------------------------- */
    openHelpModal() {
        document.getElementById('modal-help')?.classList.remove('hidden');
    },
    closeHelpModal() {
        document.getElementById('modal-help')?.classList.add('hidden');
    },
    openInstructionModal() {
        this.closeHelpModal();
        document.getElementById('modal-instruction')?.classList.remove('hidden');
    },
    closeInstructionModal() {
        document.getElementById('modal-instruction')?.classList.add('hidden');
    },
    openRegexModal() {
        this.closeHelpModal();
        document.getElementById('modal-regex')?.classList.remove('hidden');
    },
    closeRegexModal() {
        document.getElementById('modal-regex')?.classList.add('hidden');
    },
    openCompanyRegModal() {
        document.getElementById('modal-company-reg')?.classList.remove('hidden');
    },
    closeCompanyRegModal() {
        document.getElementById('modal-company-reg')?.classList.add('hidden');
    },

    switchHelpTab(tab) {
        const tabErr = document.getElementById('help-tab-error');
        const tabRev = document.getElementById('help-tab-review');
        const starContainer = document.getElementById('star-rating-container');

        if (tab === 'error') {
            tabErr.className = "text-[#00f3ff] pb-1 border-b-2 border-[#00f3ff]";
            tabRev.className = "text-neutral-400 pb-1";
            starContainer.classList.add('hidden');
        } else {
            tabRev.className = "text-[#00f3ff] pb-1 border-b-2 border-[#00f3ff]";
            tabErr.className = "text-neutral-400 pb-1";
            starContainer.classList.remove('hidden');
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
    },

    loadTelegramUser() {
        const user = window.Telegram?.WebApp?.initDataUnsafe?.user;
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
        } else {
            const mockName = "Tom Mastercloud";
            const mockHandle = "@Heytomhadir2310";
            const mockId = "ID: 8269470905";

            if (nameEl) nameEl.innerText = mockName;
            if (handleEl) handleEl.innerText = mockHandle;
            if (idEl) idEl.innerText = mockId;

            if (dName) dName.innerText = `${mockName} 👑`;
            if (dHandle) dHandle.innerText = mockHandle;

            if (pName) pName.innerText = `${mockName} 👑`;
            if (pHandle) pHandle.innerText = mockHandle;
            if (pId) pId.innerText = mockId;

            [initialsEl, dInitials, pInitials].forEach(init => {
                if (init) init.innerText = "TM";
            });
        }
    },

    loadAffiliateLink() {
        const user = window.Telegram?.WebApp?.initDataUnsafe?.user;
        const userId = user ? user.id : '8269470905';
        const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}`;
        const el = document.getElementById('affiliate-link-text');
        if (el) el.innerText = link;
    },

    copyAffiliateLink() {
        const user = window.Telegram?.WebApp?.initDataUnsafe?.user;
        const userId = user ? user.id : '8269470905';
        const link = `https://t.me/${CONFIG.BOT_USERNAME}?start=ref_${userId}`;
        this.copyText(link);
    },

    logout() {
        if (confirm(this.currentLang === 'es' ? "¿Deseas cerrar la sesión de The Bunker OS?" : "Close The Bunker OS session?")) {
            if (window.Telegram?.WebApp) {
                window.Telegram.WebApp.close();
            } else {
                alert(this.currentLang === 'es' ? "Sesión finalizada." : "Session terminated.");
                location.reload();
            }
        }
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
            return null;
        }
    },

    setStat(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerText = (value === null || value === undefined || value === '') ? '0' : value;
    },

    async loadStats() {
        const data = await this.apiGet(`/stats?context=${this.activeContext}`);
        this.setStat('stat-subs-count', data?.subscribers ?? 24);
        this.setStat('stat-revenue-count', data?.revenue_stars != null ? `${data.revenue_stars} ⭐` : '7,200 ⭐');
        this.setStat('stat-verified', data?.verified ?? 142);
        this.setStat('stat-expelled', data?.expelled ?? 19);
        this.setStat('stat-purges', data?.purges ?? 58);

        const profileBal = document.getElementById('profile-balance-stars');
        if (profileBal) {
            profileBal.innerText = data?.revenue_stars != null ? `${data.revenue_stars} ⭐` : '7,200 ⭐';
        }
    },

    async loadChannels() {
        const data = await this.apiGet('/channels');
        this.state.channels = (data && data.channels) || [
            {
                id: "-1002345678901",
                title: "The Bunker Live Studio",
                type: "channel",
                license_status: "active",
                members: "4,945",
                activity: [10, 35, 60, 40, 85, 70, 95],
                joined: 338,
                left: 119
            },
            {
                id: "-1009876543210",
                title: "THE RED VAULT CHAT",
                type: "channel",
                license_status: "expired",
                members: "792",
                activity: [5, 12, 18, 10, 25, 45, 90],
                joined: 33,
                left: 58
            }
        ];
        this.renderChatList('channels-list', this.state.channels, this.t('no_channels'));
        this.populateSelect('channel-owner-select', this.state.channels, this.t('no_channels'));
    },

    async loadGroups() {
        const data = await this.apiGet('/groups');
        this.state.groups = (data && data.groups) || [
            {
                id: "-1005544332211",
                title: "The Bunker Community",
                type: "supergroup",
                license_status: "active",
                members: "1,425",
                activity: [20, 40, 30, 70, 50, 65, 80],
                joined: 24,
                left: 6
            }
        ];
        this.renderChatList('groups-list', this.state.groups, this.t('no_groups'));
        this.populateSelect('group-owner-select', this.state.groups, this.t('no_groups'));
    },

    async loadSubscribers() {
        const data = await this.apiGet('/subscribers');
        this.state.subscribers = (data && data.subscribers) || [
            { username: "alex_trader", plan_name: "Pase Mensual VIP", price: 150, days_left: 28 },
            { username: "crypto_sam", plan_name: "Pase Trimestral VIP", price: 400, days_left: 2 }
        ];
        this.renderSubscriberList(this.state.subscribers);
    },

    populateSelect(id, list, emptyLabel) {
        const sel = document.getElementById(id);
        if (!sel) return;
        if (!list || list.length === 0) {
            sel.innerHTML = `<option value="">${emptyLabel}</option>`;
            return;
        }
        sel.innerHTML = list.map(c => `<option value="${c.id}">${c.type === 'channel' ? '📢' : '🛡️'} ${c.title} (ID: ${c.id})</option>`).join('');
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
        const statusHtml = licenseActive
            ? `<span class="text-emerald-400 font-bold text-[10px] shrink-0">${this.t('license_active')}</span>`
            : `<button onclick="app.renewLicense('${chat.id}')" class="bg-rose-500/20 text-rose-400 border border-rose-500/50 px-2.5 py-1 rounded-lg text-[10px] font-bold hover:bg-rose-500/30 active:scale-95 transition shrink-0">${this.t('license_renew')}</button>`;

        const deltaHtml = (chat.joined != null)
            ? `<span class="text-emerald-400">+${chat.joined}</span> <span class="text-rose-400 ml-1.5">-${chat.left}</span>`
            : `<span class="text-neutral-500">—</span>`;

        return `
        <div class="glass-panel p-3.5 space-y-2.5" data-chat-id="${chat.id}">
            <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2 min-w-0">
                    <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-[#00f3ff]/25 to-[#ff00ff]/25 flex items-center justify-center shrink-0 overflow-hidden border border-white/10">
                        <i class="fa-solid ${isChannel ? 'fa-tower-broadcast' : 'fa-shield-halved'} text-[#00f3ff] text-xs"></i>
                    </div>
                    <div class="min-w-0">
                        <p class="text-xs font-bold text-theme-main truncate max-w-[130px]">${chat.title}</p>
                        <p class="text-[9px] text-theme-muted font-mono flex items-center gap-1"><i class="fa-solid fa-user text-[8px]"></i> ${chat.members ?? '0'}</p>
                    </div>
                </div>
                ${statusHtml}
            </div>
            <div class="h-16 w-full">${this.generateSparkline(chat.activity || [5, 15, 10, 25, 20, 30], isChannel ? '#00f3ff' : '#39ff88')}</div>
            <div class="flex items-center justify-between border-t border-neutral-800 pt-2 font-mono">
                <span class="text-[9px]">${deltaHtml}</span>
                <button onclick="app.configureChat('${chat.id}')" class="text-[9px] text-[#00f3ff] font-bold uppercase flex items-center gap-1 hover:text-white transition">
                    <i class="fa-solid fa-gear"></i> ${this.t('configure')}
                </button>
            </div>
        </div>`;
    },

    renderChatList(containerId, list, emptyMsg) {
        const el = document.getElementById(containerId);
        if (!el) return;
        if (!list || list.length === 0) {
            el.innerHTML = `<div class="glass-panel p-6 text-center text-xs text-neutral-500 font-mono">${emptyMsg}</div>`;
            return;
        }
        el.innerHTML = list.map(c => this.chatCardTemplate(c)).join('');
    },

    subscriberTemplate(s) {
        const daysLeft = s.days_left ?? 0;
        const statusColor = daysLeft > 5 ? 'emerald-400' : (daysLeft > 0 ? 'amber-400' : 'rose-400');
        const daysLabel = daysLeft > 0 ? `${daysLeft} ${this.currentLang === 'es' ? 'días restantes' : 'days left'}` : 'Período de Gracia 🔴';
        return `
        <div class="bg-black/60 p-3.5 rounded-xl border border-neutral-800 flex items-center justify-between font-mono">
            <div>
                <p class="text-white font-bold">@${s.username}</p>
                <p class="text-[10px] text-neutral-400">${s.plan_name} (${s.price} ⭐)</p>
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

    renewLicense(chatId) {
        this.switchTab('plans');
    },

    configureChat(chatId) {
        this.switchTab('channels');
    },

    openChannelStudio() {
        const targetLink = document.getElementById('studio-target-link')?.value || '';
        const price = document.getElementById('studio-stars-price')?.value || '150';
        const days = document.getElementById('studio-duration-days')?.value || '30';

        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
        }

        alert(this.currentLang === 'es'
            ? `📡 Sincronización del Estudio:\n\n• Tarifa: ${price} Stars (XTR)\n• Duración: ${days} días\n• Destino VIP: ${targetLink}`
            : `📡 Studio Synchronized:\n\n• Rate: ${price} Stars (XTR)\n• Duration: ${days} days\n• VIP Target: ${targetLink}`);
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
                this.updateWalletUI(this.tonConnectUI.connected ? this.tonConnectUI.account : null);
                this.tonConnectUI.onStatusChange(async (wallet) => this.updateWalletUI(wallet?.account));
            } catch (err) {
                console.warn('[TON Connect]', err);
            }
        }
    },

    async connectWallet() {
        try {
            if (!this.tonConnectUI) await this.initTonConnect();
            if (!this.tonConnectUI) return;

            if (this.tonConnectUI.connected) {
                if (confirm(this.currentLang === 'es' ? "¿Desconectar TON Wallet?" : "Disconnect TON Wallet?")) {
                    await this.tonConnectUI.disconnect();
                    this.updateWalletUI(null);
                }
            } else {
                await this.tonConnectUI.openModal();
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
            const result = await this.tonConnectUI.sendTransaction(tx);
            if (result) {
                alert(this.currentLang === 'es' ? "✅ Transacción exitosa. ¡Activación procesada!" : "✅ Transaction successful.");
                this.closeCheckout();
            }
        } catch (err) {
            console.warn('Tx cancelada:', err);
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
                alert(this.currentLang === 'es' ? "⚠️ Conecta tu TON Wallet primero." : "⚠️ Connect your TON Wallet first.");
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
            alert(this.currentLang === 'es' ? "¡Copiado al portapapeles! 📋" : "Copied to clipboard! 📋");
        });
    }
};

window.app = app;
document.addEventListener('DOMContentLoaded', () => app.init());