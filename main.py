import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from database.database import init_db
from middlewares.anti_spam import AntiSpamMiddleware
from handlers import (
    payments, 
    user_private, 
    ecosystem, 
    moderation, 
    vc_manager, 
    admin_group, 
    groups
)
from assistant import (
    start_voice_radar, 
    close_all_sentinels
)

# Cargar variables de entorno locales o de Railway
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8801126106:AAH2uxiHrU2g4zhtdMn3H_iGZ0pjkaXaSqQ")
ADMIN_GROUP_ID_RAW = os.getenv("ADMIN_GROUP_ID", "-1004351489258")
ADMIN_GROUP_ID = int(ADMIN_GROUP_ID_RAW) if ADMIN_GROUP_ID_RAW else None


async def main():
    # 1. Configuración de logging unificado y limpio
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
        stream=sys.stdout
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    # 2. Inicializar la base de datos SQLite y matrices de seguridad
    init_db()
    print("🛡️ [Base de Datos]: Esquema relacional, matrices perimetrales y programador VC inicializados con éxito.")

    # 3. Inicializar Bot con parse_mode HTML por defecto
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # 4. Registrar Middleware Anti-Spam global
    dp.message.middleware(AntiSpamMiddleware())

    # 5. ORDEN ESTRATÉGICO DE ENRUTAMIENTO (De lo prioritario y transaccional a lo general):
    # - payments: Facturación, Stars y pre-checkouts sin interferencias
    # - user_private: Navegación de menús y comandos /start en DM
    # - admin_group: Comandos directos del Dueño con avisos tagueados
    # - ecosystem: Radares, telemetría y consultas del estado del búnker
    # - moderation: Consola remota y callbacks de sanción
    # - vc_manager: Comandos de videollamada y control acústico
    # - groups: Escáner perimetral de mensajes, cerraduras, anti-flood y captcha (al final)
    dp.include_router(payments.router)      
    dp.include_router(user_private.router)  
    dp.include_router(admin_group.router)   
    dp.include_router(ecosystem.router)     
    dp.include_router(moderation.router)    
    dp.include_router(vc_manager.router)    
    dp.include_router(groups.router)        

    # 6. Desplegar clúster de Centinelas y Programador VC en segundo plano
    print("📡 [Radar MTProto]: Desplegando clúster de Centinelas y Programador VC...")
    try:
        start_voice_radar(bot)
    except Exception as e:
        print(f"⚠️ [Radar MTProto Aviso]: No se pudo iniciar el gestor de centinelas: {e}")

    print("🚀 ¡El Búnker Bot está completamente operativo y listo para producción, Rafa!")

    try:
        # Purgar actualizaciones viejas para evitar conflictos al arrancar
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Suscribir todos los eventos requeridos (incluyendo pagos en Stars y solicitudes de ingreso)
        allowed_updates = dp.resolve_used_update_types()
        required_updates = [
            "message", "callback_query", "pre_checkout_query", 
            "chat_join_request", "chat_member", "my_chat_member"
        ]
        for update_type in required_updates:
            if update_type not in allowed_updates:
                allowed_updates.append(update_type)

        await dp.start_polling(bot, allowed_updates=allowed_updates)
    finally:
        print("🛑 [Sistema]: Deteniendo clúster y cerrando sesiones de centinelas de forma segura...")
        await close_all_sentinels()
        await bot.session.close()
        print("🛡️ [Sistema]: El Búnker se ha cerrado de forma ordenada bajo los estándares de Cloud Media Management.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("⚠️ [Sistema]: Bot detenido manualmente por el operador.")