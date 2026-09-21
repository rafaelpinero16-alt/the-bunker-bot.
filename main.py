import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types.web_app_info import WebAppInfo

from database.database import (
    init_db, 
    get_all_active_clone_tokens, 
    get_or_create_user
)
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
from handlers.user_private import (
    get_main_keyboard, 
    get_group_panel_keyboard, 
    TEXTS
)
from assistant import (
    start_voice_radar, 
    close_all_sentinels
)

# Cargar variables de entorno
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_GROUP_ID_RAW = os.getenv("ADMIN_GROUP_ID")
WEBAPP_URL = "https://thebunkerapp.netlify.app"

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN no está definido en las variables de entorno.")
if not ADMIN_GROUP_ID_RAW:
    raise RuntimeError("❌ ADMIN_GROUP_ID no está definido en las variables de entorno.")

ADMIN_GROUP_ID = int(ADMIN_GROUP_ID_RAW)

active_clone_tasks = {}
dp = Dispatcher()


async def _clone_worker(clone_bot: Bot, token: str):
    """
    Worker de polling dedicado para clones con despacho instantáneo de la interfaz
    completa de The Bunker (estilo Group Help) y reenvío de callbacks al Dispatcher.
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
                    # 👑 GESTIÓN DIRECTA DE COMANDOS PRIVADOS PARA CLONES (100% IDÉNTICO A GROUP HELP)
                    if update.message and update.message.chat.type == "private":
                        user = update.message.from_user
                        text = (update.message.text or "").strip()
                        user_id = user.id if user else 0
                        logging.info(f"📩 [Clon @{bot_username}] Mensaje de {user_id}: '{text}'")

                        if text.startswith("/start"):
                            lang = "es" if user and user.language_code and user.language_code.startswith("es") else "en"
                            t = TEXTS.get(lang, TEXTS["es"])

                            try:
                                if user:
                                    await get_or_create_user(user.id, user.username or "Sin username", user.full_name)
                            except Exception as db_err:
                                logging.error(f"Aviso BD Clon: {db_err}")

                            # Deep-link de configuración directa de comunidad (/start gset_...)
                            parts = text.split()
                            if len(parts) > 1 and parts[1].startswith("gset_"):
                                try:
                                    g_id = int(parts[1].split("_")[1])
                                    try:
                                        g_chat = await clone_bot.get_chat(g_id)
                                        g_name = g_chat.title
                                    except Exception:
                                        g_name = "Comunidad" if lang == "es" else "Community"

                                    await clone_bot.send_message(
                                        chat_id=update.message.chat.id,
                                        text=t["group_panel_title"].format(group_name=g_name),
                                        reply_markup=get_group_panel_keyboard(g_id, lang),
                                        parse_mode="HTML"
                                    )
                                    continue
                                except Exception as gset_err:
                                    logging.error(f"Error gset en clon: {gset_err}")

                            # Despliegue de la interfaz oficial completa de The Bunker con el username del clon
                            welcome_text = t["welcome"].format(name=user.full_name if user else "Comandante")
                            full_keyboard = get_main_keyboard(bot_username, lang)

                            await clone_bot.send_message(
                                chat_id=update.message.chat.id,
                                text=welcome_text,
                                reply_markup=full_keyboard,
                                parse_mode="HTML"
                            )
                            logging.info(f"✅ [Clon @{bot_username}] Matriz completa desplegada con éxito.")
                            continue

                    # Callbacks y eventos de grupo se inyectan fluidamente al Dispatcher
                    await dp.feed_update(clone_bot, update)
                except Exception as feed_err:
                    logging.error(f"❌ [Error en clon @{bot_username}]: {feed_err}", exc_info=True)
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

    # Routers perimetrales
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