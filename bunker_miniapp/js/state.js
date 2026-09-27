/* ==========================================================================
   THE BUNKER — COMMAND OS
   state.js — Gestor de Estado Reactivo Global y Configuración de Sesión
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

export const state = {
    // 🔐 Sesión, Autenticación y Operador
    isAuthenticated: false,
    webUser: null,
    isSyncing: false,

    // 🌐 Preferencias de Interfaz y Navegación
    currentLang: 'es',
    currentTheme: 'dark',
    selectedPlan: 'pro',
    currentTab: 'dashboard',
    activeContext: 'global',
    selectedChatId: null,
    selectedRating: 5,

    // 💎 Conectividad Web3 (TON Blockchain)
    tonConnectUI: null,

    // 🛡️ Perímetro y Switches de Seguridad
    securitySwitches: {
        captcha: true,
        autolower: true,
        shield: true,
        linklock: false
    },

    // ⚡ Módulos Tácticos y Clúster Ultra Pro
    tactical: {
        cloneBotToken: '',
        cloneUsername: '',
        sentinelSession: '',
        isPurging: false,
        lastPurgeScan: null
    },

    // 📊 Colecciones de Datos en Memoria y Telemetría
    data: {
        stats: {
            subscribers: 0,
            revenue_stars: 0,
            verified: 0,
            expelled: 0,
            purges: 0
        },
        channels: [],
        groups: [],
        subscribers: [],
        currentChatDashboard: null,
        currentChatStats: null,
        currentChatAdmins: [],
        currentChatTopUsers: []
    }
};