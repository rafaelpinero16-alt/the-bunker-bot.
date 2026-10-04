/* ==========================================================================
   THE BUNKER — COMMAND OS
   api.js — Cliente HTTP Centralizado, Radar WebSocket y Comunicación con Railway API
   The Bunker Command OS © 2026 — Cloud Media Management
   ========================================================================== */

import { CONFIG } from './config.js';
import { tgApp } from './telegram.js';
import { state } from './state.js';

function buildUrl(path) {
    const base = (CONFIG.API_BASE || '').replace(/\/+$/, '');
    const cleanPath = path.startsWith('/') ? path : `/${path}`;

    // Si la base no incluye ya /api y el path tampoco, lo prefijamos
    if (!base.endsWith('/api') && !cleanPath.startsWith('/api')) {
        return `${base}/api${cleanPath}`;
    }
    return `${base}${cleanPath}`;
}

/** Mensaje legible de un cuerpo de error de FastAPI (detail puede ser string, lista u objeto). */
function errorDetail(errData, status) {
    const detail = errData && errData.detail;
    return (typeof detail === 'string' && detail.trim()) ? detail : `HTTP ${status}`;
}

export const api = {
    /**
     * GET autenticado. Devuelve el JSON o { __error, __status } (nunca lanza).
     * opts.timeoutMs aborta la petición pasado ese tiempo (__error: 'timeout').
     */
    async get(path, opts = {}) {
        let timer = null;
        try {
            const url = buildUrl(path);
            const init = { headers: tgApp.getAuthHeaders() };

            if (opts.timeoutMs && typeof AbortController !== 'undefined') {
                const controller = new AbortController();
                timer = setTimeout(() => controller.abort(), opts.timeoutMs);
                init.signal = controller.signal;
            }

            const res = await fetch(url, init);

            if (res.status === 401) {
                this.handleSessionExpired();
                return { __error: 'unauthorized', __status: 401 };
            }

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                return { __error: errorDetail(errData, res.status), __status: res.status };
            }

            return await res.json();
        } catch (err) {
            if (err && err.name === 'AbortError') {
                return { __error: 'timeout', __status: 0 };
            }
            console.error(`[API GET ERROR] ${path}:`, err);
            return { __error: 'network', __status: 0 };
        } finally {
            if (timer) clearTimeout(timer);
        }
    },

    async post(path, body = {}) {
        try {
            const url = buildUrl(path);
            const res = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...tgApp.getAuthHeaders()
                },
                body: JSON.stringify(body)
            });

            if (res.status === 401) {
                this.handleSessionExpired();
                return { __error: 'unauthorized', __status: 401 };
            }

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                return { __error: errorDetail(errData, res.status), __status: res.status };
            }

            return await res.json();
        } catch (err) {
            console.error(`[API POST ERROR] ${path}:`, err);
            return { __error: 'network', __status: 0 };
        }
    },

    handleSessionExpired() {
        if (window.Telegram?.WebApp?.initData) return;
        localStorage.removeItem('bunker_session_token');
        state.isAuthenticated = false;
        // El radar en vivo depende de la misma sesión: se cierra para no reintentar con credenciales caducadas.
        if (window.app?.disconnectLiveRadar) {
            window.app.disconnectLiveRadar();
        } else if (state.wsClient) {
            state.wsClient.close();
            state.wsClient = null;
        }
        if (window.app?.showLoginGate) {
            window.app.showLoginGate('login_expired');
        }
    },

    // --- Endpoints de Telemetría y Ecosistema ---

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

    // --- Endpoints del Dashboard de Comunidad ---

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

    // --- Analítica Unificada de Comunidad (Fase 5) ---

    /**
     * GET /api/community/{chat_id}/analytics?fresh={true|false}
     * Un único JSON con resumen, crecimiento/cohortes, mapa de calor 7x24, cuadro de honor
     * y desglose por tipo de mensaje. fresh=true se salta la caché del servidor (4 s).
     * Devuelve { __error, __status } ante cualquier fallo (403 = sin permisos, 503/504 = no disponible).
     */
    async fetchCommunityAnalytics(chatId, fresh = false) {
        const flag = fresh ? 'true' : 'false';
        return await this.get(
            `/community/${encodeURIComponent(chatId)}/analytics?fresh=${flag}`,
            { timeoutMs: CONFIG.ANALYTICS?.REST_TIMEOUT_MS || 12000 }
        );
    },

    // --- Acciones Tácticas Ultra Pro ---

    async deployBotClone(chatId, botToken) {
        return await this.updateChatSettings(chatId, {
            action: 'deploy_clone',
            bot_token: botToken
        });
    },

    async connectSentinel(chatId, sessionString) {
        return await this.updateChatSettings(chatId, {
            action: 'connect_sentinel',
            session_string: sessionString
        });
    },

    async triggerGhostPurge(chatId) {
        return await this.updateChatSettings(chatId, {
            action: 'run_ghost_purge'
        });
    },

    // --- Sesión Web y Autenticación Widget ---

    async exchangeWebToken(tempToken) {
        return await this.post('/auth/exchange-token', { token: tempToken });
    },

    async verifyWebSession(sessionToken) {
        try {
            const url = buildUrl('/auth/session-check');
            const res = await fetch(url, {
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

/* ==========================================================================
   ⚡ BunkerWebSocketClient — Radar en vivo /ws/radar/{chat_id}
   --------------------------------------------------------------------------
   • Autenticación por initData firmado de Telegram (?init_data=) o, en navegador
     web, por token de sesión (?token=). Modo 'frame': primer mensaje {action:'auth'}.
   • Reconexión exponencial con jitter; el contador solo se reinicia al recibir el
     "hello" del servidor (un cierre 4401 justo tras abrir no debe resetear el backoff).
   • Heartbeat: "ping" cada 20 s; si no llega NINGÚN mensaje en el plazo de gracia
     la conexión se da por muerta y se reabre (un socket zombi tras un corte de red
     no emite "close" durante minutos).
   • Detección de huecos con el número de secuencia "seq" del servidor.
   • Despacho de eventos: on(evento, (data, envelope) => …), on('*', (nombre, data, envelope) => …).

   Eventos del servidor: hello, pong, error, analytics_snapshot, initial_state,
   state_refresh, message, level_up, voice_presence, voice_call_started,
   voice_call_ended, stars_payment, settings_updated, subscribed, server_shutdown.
   Eventos locales: status, open, close, reconnect_scheduled, reconnected, gap, dead,
   auth_failed.

   Códigos de cierre del servidor: 4400 petición inválida · 4401 sin autenticar ·
   4403 sin permisos · 4408 inactividad · 4429 sala llena / demasiadas sesiones.
   ========================================================================== */

const LIVE_EVENTS = new Set([
    'message', 'level_up', 'voice_call_started', 'voice_call_ended',
    'voice_presence', 'stars_payment', 'settings_updated'
]);
const SNAPSHOT_EVENTS = new Set(['analytics_snapshot', 'initial_state', 'state_refresh']);
const AUTH_CLOSE_CODES = new Set([4401, 4403, 1008]);
const BAD_REQUEST_CLOSE_CODE = 4400;
const RATE_LIMIT_CLOSE_CODE = 4429;
const SERVER_ANALYTICS_COOLDOWN_MS = 2500;   // el servidor ignora refrescos < 2 s
const WS_OPEN = 1;

function deriveWsBase() {
    if (CONFIG.WS_BASE) return CONFIG.WS_BASE;
    return (CONFIG.API_BASE || '').replace(/^http/i, 'ws');
}

function safeStorageGet(key) {
    try {
        return localStorage.getItem(key);
    } catch (err) {
        return null;
    }
}

export class BunkerWebSocketClient {
    /**
     * @param {string|number} chatId   Comunidad a escuchar.
     * @param {object} [options]       Sobrescribe CONFIG.WS (y permite inyectar WebSocketImpl / getCredentials en pruebas).
     */
    constructor(chatId, options = {}) {
        const cfg = CONFIG.WS || {};
        this.chatId = String(chatId);
        this.options = {
            baseUrl: deriveWsBase(),
            authMode: cfg.AUTH_MODE || 'query',
            heartbeatMs: cfg.HEARTBEAT_MS ?? 20000,
            pongTimeoutMs: cfg.PONG_TIMEOUT_MS ?? 10000,
            backoffBaseMs: cfg.BACKOFF_BASE_MS ?? 1000,
            backoffMaxMs: cfg.BACKOFF_MAX_MS ?? 30000,
            backoffFactor: cfg.BACKOFF_FACTOR ?? 2,
            rateLimitedMinMs: cfg.RATE_LIMITED_MIN_MS ?? 30000,
            WebSocketImpl: null,
            getCredentials: null,
            ...options
        };

        this.ws = null;
        this.status = 'idle';
        this.attempt = 0;
        this.lastSeq = null;
        this.helloCount = 0;
        this.handlers = new Map();

        this._userClosed = false;
        this._paused = false;
        this._fatal = false;
        this._filtered = false;
        this._envBound = false;
        this._heartbeatTimer = null;
        this._pongTimer = null;
        this._reconnectTimer = null;
        this._analyticsTimer = null;
        this._lastAnalyticsAt = 0;

        this._onOnline = this._onOnline.bind(this);
        this._onOffline = this._onOffline.bind(this);
    }

    // ------------------------------------------------------------------
    // Estado y registro de eventos
    // ------------------------------------------------------------------
    get isOpen() {
        return !!this.ws && this.ws.readyState === WS_OPEN;
    }

    /** Registra un manejador. Devuelve la función para anularlo. */
    on(event, callback) {
        if (typeof callback !== 'function') return () => {};
        if (!this.handlers.has(event)) this.handlers.set(event, new Set());
        this.handlers.get(event).add(callback);
        return () => this.off(event, callback);
    }

    once(event, callback) {
        const off = this.on(event, (...args) => {
            off();
            callback(...args);
        });
        return off;
    }

    off(event, callback) {
        const set = this.handlers.get(event);
        if (!set) return;
        set.delete(callback);
        if (set.size === 0) this.handlers.delete(event);
    }

    _emit(event, data, envelope) {
        const run = (set, args) => {
            if (!set) return;
            [...set].forEach(cb => {
                try {
                    cb(...args);
                } catch (err) {
                    console.error(`[WS] Error en el manejador de "${event}":`, err);
                }
            });
        };
        run(this.handlers.get(event), [data, envelope]);
        run(this.handlers.get('*'), [event, data, envelope]);
    }

    _setStatus(status, detail = {}) {
        if (this.status === status) return;
        this.status = status;
        this._emit('status', { status, ...detail });
    }

    // ------------------------------------------------------------------
    // Ciclo de vida
    // ------------------------------------------------------------------
    connect() {
        this._userClosed = false;
        this._paused = false;
        this._fatal = false;
        this._bindEnv();
        this._open(false);
        return this;
    }

    /** Cierre definitivo: libera temporizadores, listeners y manejadores. Para pausas usa pause(). */
    close(code = 1000, reason = 'client_close') {
        this._userClosed = true;
        this._clearTimers();
        this._unbindEnv();
        this._dropSocket(code, reason);
        this._setStatus('idle');
        this.handlers.clear();
    }

    /** Cierra el socket sin destruir la instancia (p. ej. Mini App en segundo plano). */
    pause() {
        if (this._userClosed || this._paused) return;
        this._paused = true;
        this._clearTimers();
        this._dropSocket(1000, 'paused');
        this._setStatus('paused');
    }

    resume() {
        if (this._userClosed || !this._paused) return;
        this._paused = false;
        this._fatal = false;
        this.attempt = 0;
        this._open(false);
    }

    _dropSocket(code, reason) {
        const ws = this.ws;
        this.ws = null;
        if (!ws) return;
        ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
        try {
            ws.close(code, reason);
        } catch (err) { /* ya cerrado */ }
    }

    _clearTimers() {
        this._stopHeartbeat();
        if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
        if (this._analyticsTimer) { clearTimeout(this._analyticsTimer); this._analyticsTimer = null; }
    }

    // ------------------------------------------------------------------
    // Conexión
    // ------------------------------------------------------------------
    _credentials() {
        if (typeof this.options.getCredentials === 'function') return this.options.getCredentials();

        const initData = tgApp.tg?.initData;
        if (initData && initData.trim() !== '') return { type: 'init_data', value: initData };

        const token = safeStorageGet('bunker_session_token');
        if (token) return { type: 'token', value: token };

        const cached = safeStorageGet('bunker_init_data');
        if (cached) return { type: 'init_data', value: cached };

        return null;
    }

    _buildUrl(creds) {
        const base = String(this.options.baseUrl || '').replace(/\/+$/, '');
        const path = `/ws/radar/${encodeURIComponent(this.chatId)}`;
        if (this.options.authMode === 'frame') return `${base}${path}`;
        const key = creds.type === 'token' ? 'token' : 'init_data';
        return `${base}${path}?${key}=${encodeURIComponent(creds.value)}`;
    }

    _open(isRetry) {
        if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
        if (this._userClosed || this._paused || this._fatal || this.ws) return;

        const WS = this.options.WebSocketImpl || (typeof WebSocket !== 'undefined' ? WebSocket : null);
        if (!WS) {
            this._setStatus('offline', { reason: 'no_websocket' });
            return;
        }

        // Sin red no se intenta: el evento "online" reanuda de inmediato.
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
            this._setStatus('offline', { reason: 'navigator_offline' });
            return;
        }

        const creds = this._credentials();
        if (!creds) {
            this._fatal = true;
            this._setStatus('denied', { reason: 'no_credentials' });
            this._emit('auth_failed', { code: 4401, reason: 'no_credentials' });
            return;
        }

        this._setStatus(isRetry ? 'reconnecting' : 'connecting', { attempt: this.attempt });

        let ws;
        try {
            ws = new WS(this._buildUrl(creds));
        } catch (err) {
            console.error('[WS] No se pudo crear el WebSocket:', err);
            this._scheduleReconnect({ code: 0, reason: 'construct_error' });
            return;
        }

        this.ws = ws;
        ws.onopen = () => { if (this.ws === ws) this._onOpen(creds); };
        ws.onmessage = (ev) => { if (this.ws === ws) this._onMessage(ev); };
        ws.onerror = () => { /* el evento "close" siempre sigue a un error */ };
        ws.onclose = (ev) => { if (this.ws === ws) this._onClose(ev); };
    }

    _onOpen(creds) {
        this._emit('open', { chatId: this.chatId, attempt: this.attempt });
        if (this.options.authMode === 'frame') {
            this.send({ action: 'auth', [creds.type]: creds.value });
        }
        this._startHeartbeat();
    }

    _onMessage(ev) {
        // Cualquier mensaje prueba que la conexión sigue viva.
        if (this._pongTimer) { clearTimeout(this._pongTimer); this._pongTimer = null; }

        let envelope;
        try {
            envelope = JSON.parse(ev.data);
        } catch (err) {
            return;
        }
        if (!envelope || typeof envelope !== 'object' || typeof envelope.event !== 'string') return;

        const name = envelope.event;

        if (name === 'hello') {
            this.attempt = 0;
            this.lastSeq = Number.isFinite(Number(envelope.seq)) ? Number(envelope.seq) : null;
            const isReconnect = this.helloCount > 0;
            this.helloCount += 1;
            this._setStatus('live', { userId: envelope.data?.user_id });
            this._emit('hello', envelope.data ?? envelope, envelope);
            if (isReconnect) this._emit('reconnected', { chatId: this.chatId }, envelope);
            return;
        }

        if (SNAPSHOT_EVENTS.has(name)) {
            // Los snapshots llevan el seq vigente: re-basan el contador sin falsos huecos.
            const seq = Number(envelope.seq);
            if (Number.isFinite(seq) && (this.lastSeq === null || seq > this.lastSeq)) this.lastSeq = seq;
        } else if (LIVE_EVENTS.has(name)) {
            this._trackSeq(envelope);
        }

        this._emit(name, envelope.data ?? envelope, envelope);
    }

    _trackSeq(envelope) {
        const seq = Number(envelope.seq);
        if (!Number.isFinite(seq)) return;
        if (!this._filtered && this.lastSeq !== null && seq > this.lastSeq + 1) {
            this._emit('gap', { from: this.lastSeq, to: seq, missed: seq - this.lastSeq - 1 });
        }
        if (this.lastSeq === null || seq > this.lastSeq) this.lastSeq = seq;
    }

    _onClose(ev) {
        const code = ev && Number.isFinite(ev.code) ? ev.code : 1006;
        const reason = (ev && ev.reason) || '';
        this.ws = null;
        this._stopHeartbeat();

        if (this._userClosed || this._paused) return;

        this._emit('close', { code, reason });

        if (AUTH_CLOSE_CODES.has(code)) {
            this._fatal = true;
            this._setStatus('denied', { code, reason });
            this._emit('auth_failed', { code, reason });
            return;
        }
        if (code === BAD_REQUEST_CLOSE_CODE) {
            this._fatal = true;
            this._setStatus('denied', { code, reason });
            return;
        }
        this._scheduleReconnect({ code, reason });
    }

    // ------------------------------------------------------------------
    // Reconexión exponencial con jitter
    // ------------------------------------------------------------------
    _nextDelay() {
        const { backoffBaseMs, backoffMaxMs, backoffFactor } = this.options;
        const exp = Math.min(backoffMaxMs, backoffBaseMs * Math.pow(backoffFactor, this.attempt));
        return Math.round(exp / 2 + Math.random() * (exp / 2));   // "equal jitter": entre 50 % y 100 %
    }

    _scheduleReconnect(info) {
        if (this._userClosed || this._paused || this._fatal || this._reconnectTimer) return;

        let delay = this._nextDelay();
        if (info.code === RATE_LIMIT_CLOSE_CODE) delay = Math.max(delay, this.options.rateLimitedMinMs);
        this.attempt += 1;

        const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
        this._setStatus(offline ? 'offline' : 'reconnecting', { attempt: this.attempt });
        this._emit('reconnect_scheduled', {
            attempt: this.attempt, delayMs: delay, code: info.code, reason: info.reason
        });

        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            this._open(true);
        }, delay);
    }

    _bindEnv() {
        if (this._envBound || typeof window === 'undefined' || !window.addEventListener) return;
        window.addEventListener('online', this._onOnline);
        window.addEventListener('offline', this._onOffline);
        this._envBound = true;
    }

    _unbindEnv() {
        if (!this._envBound) return;
        window.removeEventListener('online', this._onOnline);
        window.removeEventListener('offline', this._onOffline);
        this._envBound = false;
    }

    _onOnline() {
        if (this._userClosed || this._paused || this._fatal || this.ws) return;
        this.attempt = 0;
        this._open(true);
    }

    _onOffline() {
        if (this._userClosed || this._paused) return;
        this._setStatus('offline', { reason: 'navigator_offline' });
    }

    // ------------------------------------------------------------------
    // Heartbeat (ping cada 20 s) y detección de conexiones muertas
    // ------------------------------------------------------------------
    _startHeartbeat() {
        this._stopHeartbeat();
        this._heartbeatTimer = setInterval(() => this._heartbeat(), this.options.heartbeatMs);
    }

    _stopHeartbeat() {
        if (this._heartbeatTimer) { clearInterval(this._heartbeatTimer); this._heartbeatTimer = null; }
        if (this._pongTimer) { clearTimeout(this._pongTimer); this._pongTimer = null; }
    }

    _heartbeat() {
        if (!this.isOpen) return;
        this.send('ping');
        if (this._pongTimer) clearTimeout(this._pongTimer);
        this._pongTimer = setTimeout(() => this._onDeadConnection(), this.options.pongTimeoutMs);
    }

    _onDeadConnection() {
        this._pongTimer = null;
        if (!this.ws) return;
        this._emit('dead', { chatId: this.chatId });
        this._dropSocket(4000, 'pong_timeout');
        this._stopHeartbeat();
        this._scheduleReconnect({ code: 4000, reason: 'pong_timeout' });
    }

    // ------------------------------------------------------------------
    // Envío
    // ------------------------------------------------------------------
    /** Envía texto u objeto (JSON). Devuelve false si el socket no está abierto. */
    send(payload) {
        if (!this.isOpen) return false;
        try {
            this.ws.send(typeof payload === 'string' ? payload : JSON.stringify(payload));
            return true;
        } catch (err) {
            console.error('[WS] Error enviando:', err);
            return false;
        }
    }

    _throttledAction(action) {
        const wait = SERVER_ANALYTICS_COOLDOWN_MS - (Date.now() - this._lastAnalyticsAt);
        if (wait > 0) {
            // El servidor descarta en silencio los refrescos < 2 s: se difiere el último pedido.
            if (this._analyticsTimer) clearTimeout(this._analyticsTimer);
            this._analyticsTimer = setTimeout(() => {
                this._analyticsTimer = null;
                this._throttledAction(action);
            }, wait);
            return true;
        }
        this._lastAnalyticsAt = Date.now();
        return this.send({ action });
    }

    /** Pide un analytics_snapshot nuevo (respeta el cooldown del servidor). */
    requestAnalytics() {
        return this._throttledAction('analytics');
    }

    /** Pide state_refresh (telemetría heredada) + analytics_snapshot. */
    requestRefresh() {
        return this._throttledAction('refresh');
    }

    /** Filtra los eventos en vivo recibidos (analytics_snapshot, pong, hello… siempre llegan). */
    subscribe(events) {
        this._filtered = Array.isArray(events) && events.length > 0;
        return this.send({ action: 'subscribe', events: Array.isArray(events) ? events : [] });
    }
}