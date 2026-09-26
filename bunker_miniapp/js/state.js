/* ==========================================================================
   THE BUNKER — COMMAND OS
   state.js — Gestor de Estado Reactivo Global y Configuración de Sesión
   ========================================================================== */

export const state = {
    // Sesión y Autenticación
    isAuthenticated: false,
    webUser: null,
    isSyncing: false,

    // Preferencias y UI
    currentLang: 'es',
    currentTheme: 'dark',
    selectedPlan: 'pro',
    currentTab: 'dashboard',
    activeContext: 'global',
    selectedChatId: null,
    selectedRating: 5,

    // Conectividad Web3
    tonConnectUI: null,

    // Perímetro y Switches de Seguridad
    securitySwitches: {
        captcha: true,
        autolower: true,
        shield: true,
        linklock: false
    },

    // Colecciones de Datos en Memoria
    data: {
        channels: [],
        groups: [],
        subscribers: [],
        currentChatDashboard: null,
        currentChatStats: null,
        currentChatAdmins: [],
        currentChatTopUsers: []
    }
};