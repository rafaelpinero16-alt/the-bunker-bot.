import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from database.database import init_db, get_db_connection
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

# Diccionario global para almacenar las tareas de los bots clones activos: {token: task}
active_clone_tasks = {}
dp = Dispatcher()
_global_dp_initialized = False

def get_all_active_clones() -> list:
    """Consulta en la base de datos todos los tokens de bots clones registrados."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT bot_token FROM bot_clones WHERE bot_token != ''")
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows if row[0]]
    except Exception as e:
        logging.error(f"⚠️ [Clones DB Error]: No se pudieron leer los clones activos: {e}")
        return []

async def start_clone_polling_task(token: str):
    """Inicia un ciclo de polling independiente para un bot clon específico."""
    if token in active_clone_tasks:
        return  # Ya está corriendo

    try:
        clone_bot = Bot(
            token=token, 
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        
        # Omitir actualizaciones viejas para este clon
        await clone_bot.delete_webhook(drop_pending_updates=True)
        
        logging.info(f"🧬 [Bot Clon Activo]: Desplegando instancia para el token {token[:10]}...")
        
        # Ejecutar polling en segundo plano usando el dispatcher general
        task = asyncio.create_task(dp.start_polling(clone_bot, handled_exceptions=True))
        active_clone_tasks[token] = {
            "bot": clone_bot,
            "task": task
        }
    except Exception as e:
        logging.error(f"⚠️ [Error al arrancar Bot Clon {token[:10]}]: {e}")

def trigger_dynamic_clone(token: str):
    """Función llamada desde user_private.py al registrar con éxito un nuevo token."""
    asyncio.create_task(start_clone_polling_task(token))


async def main():
    global _global_dp_initialized
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

    # 3. Inicializar Bot Maestro
    master_bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # 4. Registrar Middleware Anti-Spam global en el Dispatcher
    dp.message.middleware(AntiSpamMiddleware())

    # 5. ORDEN ESTRATÉGICO DE ENRUTAMIENTO
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
        start_voice_radar(master_bot)
    except Exception as e:
        print(f"⚠️ [Radar MTProto Aviso]: No se pudo iniciar el gestor de centinelas: {e}")

    # 7. CARGAR Y ARRANCAR TODOS LOS BOTS CLONES EXISTENTES EN LA BD
    print("🧬 [Gestor de Clones]: Sincronizando bots clones registrados en la base de datos...")
    stored_clones = get_all_active_clones()
    for clone_token in stored_clones:
        await start_clone_polling_task(clone_token)

    print(f"🚀 ¡El Búnker Bot Maestro y {len(stored_clones)} Clones están completamente operativos, Rafa!")

    try:
        # Purgar actualizaciones viejas del bot maestro
        await master_bot.delete_webhook(drop_pending_updates=True)
        
        allowed_updates = dp.resolve_used_update_types()
        required_updates = [
            "message", "callback_query", "pre_checkout_query", 
            "chat_join_request", "chat_member", "my_chat_member"
        ]
        for update_type in required_updates:
            if update_type not in allowed_updates:
                allowed_updates.append(update_type)

        # Iniciar polling del bot maestro
        await dp.start_polling(master_bot, allowed_updates=allowed_updates)
    finally:
        print("🛑 [Sistema]: Deteniendo clúster, clones y cerrando sesiones de forma segura...")
        for data in active_clone_tasks.values():
            try:
                data["task"].cancel()
                await data["bot"].session.close()
            except Exception:
                pass
        await close_all_sentinels()
        await master_bot.session.close()
        print("🛡️ [Sistema]: El Búnker se ha cerrado de forma ordenada bajo los estándares de Cloud Media Management.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("⚠️ [Sistema]: Bot detenido manualmente por el operador.")