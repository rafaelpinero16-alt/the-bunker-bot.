import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from database.database import init_db, get_all_active_clone_tokens
from middlewares.anti_spam import AntiSpamMiddleware
from handlers import (
    user_private, 
    payments, 
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

# Cargar variables de entorno
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID_RAW = os.getenv("ADMIN_GROUP_ID")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN no está definido en las variables de entorno.")
if not ADMIN_GROUP_ID_RAW:
    raise RuntimeError("❌ ADMIN_GROUP_ID no está definido en las variables de entorno.")

ADMIN_GROUP_ID = int(ADMIN_GROUP_ID_RAW)

active_clone_tasks = {}
dp = Dispatcher()


async def _clone_worker(clone_bot: Bot, token: str):
    """
    Worker de polling dedicado para clones que alimenta el Dispatcher central
    enrutando todas las interacciones (mensajes y callbacks) hacia la matriz universal.
    """
    allowed_updates = [
        "message", "callback_query", "pre_checkout_query", 
        "chat_join_request", "chat_member", "my_chat_member"
    ]
    
    try:
        await clone_bot.delete_webhook(drop_pending_updates=True)
        bot_info = await clone_bot.get_me()
        bot_username = bot_info.username or "BotClon"
        logging.info(f"🧬 [Bot Clon Activo]: Poller iniciado para @{bot_username} (ID: {bot_info.id}).")
    except Exception as e:
        logging.error(f"❌ [Error Handshake Clon {token[:10]}]: {e}")
        return

    offset = None
    while token in active_clone_tasks:
        try:
            updates = await clone_bot.get_updates(
                offset=offset, 
                timeout=15, 
                allowed_updates=allowed_updates
            )
            for update in updates:
                offset = update.update_id + 1
                try:
                    if update.message:
                        user_id = update.message.from_user.id if update.message.from_user else "N/A"
                        text = update.message.text or "[Media/Otro]"
                        logging.info(f"📩 [Clon @{bot_username}] Update #{update.update_id} | User: {user_id} | Texto: '{text}'")
                    elif update.callback_query:
                        user_id = update.callback_query.from_user.id if update.callback_query.from_user else "N/A"
                        logging.info(f"🔘 [Clon @{bot_username}] Callback #{update.update_id} | User: {user_id} | Data: '{update.callback_query.data}'")

                    # Inyección limpia al Dispatcher universal de aiogram 3
                    await dp.feed_update(clone_bot, update)
                except Exception as feed_err:
                    logging.error(f"❌ [Error feed_update clon @{bot_username}]: {feed_err}", exc_info=True)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning(f"⚠️ [Loop Clon @{bot_username}]: {e}")
            await asyncio.sleep(2)


async def start_clone_polling_task(token: str):
    """Instancia y levanta la tarea de escucha en segundo plano para un bot clon."""
    if not token or token in active_clone_tasks:
        return

    try:
        clone_bot = Bot(
            token=token, 
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        task = asyncio.create_task(_clone_worker(clone_bot, token))
        active_clone_tasks[token] = {
            "bot": clone_bot,
            "task": task
        }
    except Exception as e:
        logging.error(f"⚠️ [Error Inicializando Bot Clon {token[:10]}]: {e}")


async def stop_clone_polling_task(token: str):
    """Detiene la tarea del clon y cierra su sesión de forma limpia."""
    task_data = active_clone_tasks.pop(token, None)
    if task_data:
        task_data["task"].cancel()
        try:
            await task_data["bot"].session.close()
        except Exception:
            pass
        logging.info(f"🛑 [Bot Clon Desconectado]: Instancia {token[:10]} liberada de RAM.")


def trigger_dynamic_clone(token: str):
    """Disparador dinámico llamado al registrar un nuevo token."""
    asyncio.create_task(start_clone_polling_task(token))


def trigger_disconnect_clone(token: str):
    """Disparador dinámico para desconectar un clon."""
    asyncio.create_task(stop_clone_polling_task(token))


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
        stream=sys.stdout
    )
    logging.getLogger("aiogram.event").setLevel(logging.INFO)

    init_db()
    print("🛡️ [Base de Datos]: Esquema relacional y matrices perimetrales inicializadas.")

    master_bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp.message.middleware(AntiSpamMiddleware())

    # Routers con prioridad a comandos privados universales
    dp.include_router(user_private.router)
    dp.include_router(payments.router)
    dp.include_router(admin_group.router)
    dp.include_router(ecosystem.router)
    dp.include_router(moderation.router)
    dp.include_router(vc_manager.router)
    dp.include_router(groups.router)

    print("📡 [Radar MTProto]: Desplegando clúster de Centinelas...")
    try:
        start_voice_radar(master_bot)
    except Exception as e:
        print(f"⚠️ [Radar MTProto Aviso]: No se pudo iniciar el gestor de centinelas: {e}")

    print("🧬 [Gestor de Clones]: Sincronizando bots clones...")
    try:
        stored_clones = await get_all_active_clone_tokens()
        for clone_token in stored_clones:
            await start_clone_polling_task(clone_token)
        print(f"🚀 ¡El Búnker Bot Maestro y {len(stored_clones)} Clones están completamente operativos, Rafa!")
    except Exception as e:
        print(f"⚠️ [Aviso Clones BD]: No se pudieron precargar los clones: {e}")

    try:
        await master_bot.delete_webhook(drop_pending_updates=True)
        
        allowed_updates = dp.resolve_used_update_types()
        required_updates = [
            "message", "callback_query", "pre_checkout_query", 
            "chat_join_request", "chat_member", "my_chat_member"
        ]
        for update_type in required_updates:
            if update_type not in allowed_updates:
                allowed_updates.append(update_type)

        await dp.start_polling(master_bot, allowed_updates=allowed_updates)
    finally:
        print("🛑 [Sistema]: Deteniendo clúster y cerrando sesiones...")
        for token in list(active_clone_tasks.keys()):
            await stop_clone_polling_task(token)
        await close_all_sentinels()
        await master_bot.session.close()
        print("🛡️ [Sistema]: El Búnker se ha cerrado de forma ordenada.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("⚠️ [Sistema]: Bot detenido manualmente por el operador.")