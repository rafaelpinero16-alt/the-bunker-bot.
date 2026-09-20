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
    assistant_app, 
    start_voice_radar, 
    close_all_sentinels
)

# Cargar variables de entorno de forma segura
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8801126106:AAH2uxiHrU2g4zhtdMn3H_iGZ0pjkaXaSqQ")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "-1004347871259"))

async def main():
    # 1. Inicializar la base de datos relacional, tablas y matrices de seguridad
    init_db()
    print("🛡️ [Base de Datos]: Núcleo del Búnker, matrices y programador VC inicializados correctamente.")

    # 2. Configurar Bot y Dispatcher con parse_mode HTML por defecto
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # 3. Registrar Middleware Anti-Spam global e inmunidad táctica
    dp.message.middleware(AntiSpamMiddleware())

    # 4. ORDEN ESTRATÉGICO DE ENRUTAMIENTO (De lo específico a lo general):
    dp.include_router(payments.router)      
    dp.include_router(user_private.router)  
    dp.include_router(ecosystem.router)     
    dp.include_router(moderation.router)    
    dp.include_router(vc_manager.router)    
    dp.include_router(admin_group.router)   
    dp.include_router(groups.router)        

    # Configuración de Logging corporativo
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    # 5. Encender el motor Multi-Sentinela, Watchdog y Programador VC concurrente
    print("📡 [Radar MTProto]: Desplegando clúster de Centinelas, Watchdog y Automatización VC...")
    try:
        start_voice_radar(bot)
    except Exception as e:
        print(f"⚠️ [Radar MTProto Aviso]: No se pudo iniciar el gestor de centinelas: {e}")

    print("¡El Búnker Bot está encendido y operando en modo Multi-Comunidad, Rafa! 🤖🚀")
    
    try:
        # Purgar actualizaciones acumuladas y suscribir todos los tipos de eventos requeridos
        await bot.delete_webhook(drop_pending_updates=True)
        
        allowed_updates = dp.resolve_used_update_types()
        if "chat_join_request" not in allowed_updates:
            allowed_updates.append("chat_join_request")
        if "chat_member" not in allowed_updates:
            allowed_updates.append("chat_member")
        if "my_chat_member" not in allowed_updates:
            allowed_updates.append("my_chat_member")

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