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
    deepLinkTab: null,          // pestaña de destino cuando la Mini App se abre con ?chat_id= / startapp=

    // ⚡ Radar en vivo (WebSocket /ws/radar/{chat_id})
    wsClient: null,             // instancia activa de BunkerWebSocketClient (una por comunidad)
    wsStatus: 'idle',           // idle | connecting | live | reconnecting | offline | paused | denied
    liveAnalytics: null,        // último snapshot analítico recibido (REST o WebSocket) + ajustes en caliente
    liveTelemetry: null,        // último estado de telemetría heredado (initial_state / state_refresh)
    liveVoice: {                // sala de audio, alimentada por voice_* del radar
        active: false,
        callId: null,
        participants: 0,
        mics: 0
    },
    liveFeed: [],               // últimos eventos en vivo mostrados en el panel "Actividad en Vivo"
    liveToastsEnabled: true,    // alertas flotantes (persistido en localStorage: bunker_live_toasts)
    chatEpoch: 0,               // se incrementa al cambiar de comunidad; descarta respuestas obsoletas

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