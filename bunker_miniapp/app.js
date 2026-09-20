const translations = {
    es: {
        plans_title: "Membresías de Comando",
        basic_badge: "ACCESO GRATUITO",
        basic_title: "Plan BÁSICO",
        basic_desc: "Comandos de moderación limitados. Sin automatización de radar ni soporte prioritario.",
        btn_free_active: "Activo por defecto",
        pro_badge: "BÁSICO MENSUAL",
        pro_title: "Plan PRO",
        pro_desc: "Automatización total, moderación de voz y comandos ilimitados para tu comunidad.",
        ultra_badge: "ACCESO ILIMITADO",
        ultra_title: "ULTRA PRO",
        ultra_desc: "Radar avanzado anti-fantasmas, prioridad máxima en servidores y soporte directo.",
        tactical_gateways: "PASARELAS TÁCTICAS",
        loading_user: "Usuario Admin",
        verifying: "@admin_bunker"
    },
    en: {
        plans_title: "Command Memberships",
        basic_badge: "FREE TIER",
        basic_title: "BASIC Plan",
        basic_desc: "Limited moderation commands. No radar automation or priority support.",
        btn_free_active: "Active by Default",
        pro_badge: "MONTHLY BASIC",
        pro_title: "PRO Plan",
        pro_desc: "Total automation, voice moderation, and unlimited commands for your community.",
        ultra_badge: "UNLIMITED ACCESS",
        ultra_title: "ULTRA PRO",
        ultra_desc: "Advanced anti-ghost radar, max server priority, and direct support.",
        tactical_gateways: "TACTICAL GATEWAYS",
        loading_user: "Admin User",
        verifying: "@admin_bunker"
    }
};

const app = {
    tonConnectUI: null,
    currentLang: 'es', 
    selectedPlan: 'pro',

    init() {
        const tg = window.Telegram?.WebApp;
        if (tg) {
            tg.expand();
            tg.ready();
        }
        this.loadTelegramUser();
        this.initTonConnect();
        this.updateTranslations();
    },

    toggleLanguage() {
        this.currentLang = this.currentLang === 'es' ? 'en' : 'es';
        const indicator = document.getElementById('lang-indicator');
        if (indicator) indicator.innerText = this.currentLang.toUpperCase();
        
        if (window.Telegram?.WebApp?.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
        }
        this.updateTranslations();
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
        const imgEl = document.getElementById('avatar-img');
        const initialsEl = document.getElementById('avatar-initials');

        if (user) {
            nameEl.innerText = `${user.first_name} ${user.last_name || ''}`.trim();
            handleEl.innerText = user.username ? `@${user.username}` : `ID: ${user.id}`;
            const initials = user.first_name.charAt(0) + (user.last_name ? user.last_name.charAt(0) : '');
            
            if (user.photo_url) {
                imgEl.src = user.photo_url;
                imgEl.classList.remove('hidden');
                initialsEl.classList.add('hidden');
            } else {
                initialsEl.innerText = initials.toUpperCase();
            }
        } else {
            nameEl.innerText = translations[this.currentLang].loading_user;
            handleEl.innerText = translations[this.currentLang].verifying;
            initialsEl.innerText = "AD";
        }
    },

    async fetchTonBalance(address) {
        try {
            const res = await fetch(`https://toncenter.com/api/v2/getAddressBalance?address=${address}`);
            const data = await res.json();
            if (data.ok) {
                return (parseInt(data.result) / 1e9).toFixed(2);
            }
        } catch(e) { console.error("Error obteniendo balance:", e); }
        return "0.00";
    },

    async updateWalletUI(account) {
        const btnText = document.getElementById('wallet-btn-text');
        const statusDot = document.getElementById('wallet-status-dot');
        
        if (account) {
            const addr = account.address;
            btnText.innerText = "Cargando...";
            statusDot.className = "w-2 h-2 rounded-full bg-emerald-400 mr-1.5 shadow-[0_0_8px_rgba(52,211,153,0.8)] animate-pulse";
            
            const balance = await this.fetchTonBalance(addr);
            const shortAddr = addr.slice(0, 4) + '...' + addr.slice(-4);
            btnText.innerText = `${shortAddr} | ${balance} TON`;
            statusDot.classList.remove('animate-pulse');
        } else {
            btnText.innerText = "TON Wallet";
            statusDot.className = "w-2 h-2 rounded-full bg-amber-500 mr-1.5 animate-pulse";
        }
    },

    async initTonConnect() {
        const TonConnectClass = window.TON_CONNECT_UI?.TonConnectUI || window.TonConnectUI;
        if (!this.tonConnectUI && TonConnectClass) {
            try {
                this.tonConnectUI = new TonConnectClass({ 
                    manifestUrl: "https://alpha-bunker-backend-production.up.railway.app/tonconnect-manifest.json",
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
                console.warn("[TON Connect] Esperando inicialización:", err);
            }
        }
    },

    async connectWallet() {
        try {
            if (!this.tonConnectUI) await this.initTonConnect();
            if (!this.tonConnectUI) { alert("⚠️ TON Connect no disponible."); return; }
            
            if (this.tonConnectUI.connected) {
                if (confirm(this.currentLang === 'es' ? "¿Deseas desconectar tu TON Wallet?" : "Disconnect TON Wallet?")) {
                    await this.tonConnectUI.disconnect();
                    this.updateWalletUI(null); 
                }
            } else {
                await this.tonConnectUI.openModal();
            }
        } catch (e) {
            console.error("Error conectando wallet:", e);
        }
    },

    async executeTonPayment(plan) {
        const DESTINATION_WALLET = "UQAAnX4bGBzI0ujk35-XChap_wZ7x67NeJ85C_M1YIvLbYUF"; 
        const nanoAmount = plan === 'pro' ? "1000000000" : "1600000000"; 

        const tx = {
            validUntil: Math.floor(Date.now() / 1000) + 300,
            messages: [{ address: DESTINATION_WALLET, amount: nanoAmount }]
        };

        try {
            const result = await this.tonConnectUI.sendTransaction(tx);
            if (result) {
                alert(this.currentLang === 'es' ? "✅ Transacción exitosa. ¡Grupo activado!" : "✅ Transaction successful.");
                this.closeCheckout();
            }
        } catch (err) {
            console.warn("Transacción fallida/cancelada:", err);
        }
    },

    pagarPlan(plan, method) {
        this.selectedPlan = plan;
        
        if (method === 'stars') {
            const param = plan === 'pro' ? 'sub_pro' : 'sub_ultra';
            // 🚀 FIX: Dispara el enlace y cierra la Mini App automáticamente
            if (window.Telegram?.WebApp) {
                window.Telegram.WebApp.openTelegramLink(`https://t.me/thebunkerapp_bot?start=${param}`);
                setTimeout(() => {
                    window.Telegram.WebApp.close();
                }, 300);
            }
        } else if (method === 'paypal') {
            const price = plan === 'pro' ? '5' : '8';
            window.open(`https://paypal.me/Felipecosmic/${price}`, "_blank");
        } else if (method === 'ton') {
            if (!this.tonConnectUI || !this.tonConnectUI.connected) {
                alert(this.currentLang === 'es' ? "⚠️ Conecta tu TON Wallet primero." : "⚠️ Connect your TON Wallet first.");
                this.connectWallet();
                return;
            }
            this.executeTonPayment(plan);
        } else if (method === 'external') {
            document.getElementById('checkout-plan-name').innerText = plan === 'pro' ? 'PRO ($5.00)' : 'ULTRA PRO ($8.00)';
            document.getElementById('modal-external-checkout')?.classList.remove('hidden');
        }
    },

    processOneClickPay(gateway) {
        const priceUrl = this.selectedPlan === 'pro' ? '5.00' : '8.00';
        const links = {
            skrill: `https://skrill.me/rq/Felipe%20Rafael/${priceUrl}/USD?key=7AR7OlqodIdbV_WU4hSXJ435Na1`,
            binance: "https://app.binance.com/uni-qr/request-to-pay?billOrderId=452405181270605824&billType=request_a_payment"
        };
        
        if (links[gateway]) {
            window.open(links[gateway], "_blank", "noopener,noreferrer");
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
        navigator.clipboard.writeText(text).then(() => alert(this.currentLang === 'es' ? "¡Copiado al portapapeles! 📋" : "Copied to clipboard! 📋"));
    }
};

window.app = app;
document.addEventListener("DOMContentLoaded", () => app.init());