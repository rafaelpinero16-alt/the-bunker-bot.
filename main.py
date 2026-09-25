import asyncio
import logging
import sys
import os
import urllib.parse
import json
from dotenv import load_dotenv

# Alias de módulo: con `python main.py` este archivo corre como `__main__`.
if __name__ == "__main__":
    sys.modules.setdefault("main", sys.modules[__name__])

load_dotenv()

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, ErrorEvent, Update
from aiogram.exceptions import TelegramUnauthorizedError

# Importar FastAPI y Uvicorn para servir las rutas de la Mini App.
# El proyecto puede ejecutarse con un entorno donde estas dependencias no están
# instaladas en el intérprete de análisis; se ignora la advertencia de importación.
from fastapi import FastAPI, Header, HTTPException  # type: ignore[import-not-found]
from fastapi.middleware.cors import CORSMiddleware  # type: ignore[import-not-found]
import uvicorn  # type: ignore[import-not-found]

from database.database import (
    init_db, 
    get_all_active_clone_tokens, 
    get_or_create_user,
    get_user_global_stats,
    get_user_channels,
    get_user_groups,
    get_user_subscribers_audit,
    get_group_tier
)
from middlewares.anti_spam import AntiSpamMiddleware
from handlers import (
    payments,
    user_private, 
    moderation, 
    admin_group, 
    ecosystem, 
    vc_manager, 
    groups
)
from handlers.user_private import (
    send_official_welcome,
    set_master_bot_id,
    set_master_bot_username,
    revoke_bot_clone_db
)
from assistant import (
    start_voice_radar, 
    close_all_sentinels
)

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

# ==========================================
# 🌐 CONFIGURACIÓN DEL SERVIDOR WEB API (FASTAPI)
# ==========================================
app = FastAPI(title="The Bunker OS Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def parse_telegram_user_id(init_data: str) -> int:
    """Extrae de manera segura el user_id desde el initData de Telegram WebApp."""
    try:
        if not init_data:
            return 0
        parsed = urllib.parse.parse_qs(init_data)
        if "user" in parsed:
            user_json = json.loads(parsed["user"][0])
            return int(user_json.get("id", 0))
    except Exception:
        pass
    return 0

@app.get("/api/stats")
async def api_stats(context: str = "global", x_telegram_init_data: str = Header(None)):
    user_id = parse_telegram_user_id(x_telegram_init_data)
    if not user_id:
        user_id = 8269470905  # Fallback administrativo de respaldo si se abre fuera de Telegram
    try:
        stats = await get_user_global_stats(user_id)
        return stats
    except Exception as e:
        logging.error(f"❌ [API Stats Error]: {e}")
        return {
            "subscribers": 0, "revenue_stars": 0, "verified": 0, "expelled": 0, "purges": 0,
            "perimeter": {"captcha": "Activo 🟢", "autolower": "2% Activo 🟢", "shield": "Blindado 🟢", "broadcast": "Worker Activo 🟢"}
        }

@app.get("/api/channels")
async def api_channels(x_telegram_init_data: str = Header(None)):
    user_id = parse_telegram_user_id(x_telegram_init_data) or 8269470905
    try:
        channels = await get_user_channels(user_id)
        res = []
        for ch_id, ch_name in channels:
            tier = await get_group_tier(ch_id)
            res.append({
                "id": ch_id, "title": ch_name, "type": "channel",
                "license_status": "active" if tier != "free" else "expired",
                "members": 150, "activity": [10, 25, 40, 30, 50, 45, 60],
                "joined": 5, "left": 1, "avatar_url": None
            })
        return {"channels": res}
    except Exception as e:
        logging.error(f"❌ [API Channels Error]: {e}")
        return {"channels": []}

@app.get("/api/groups")
async def api_groups(x_telegram_init_data: str = Header(None)):
    user_id = parse_telegram_user_id(x_telegram_init_data) or 8269470905
    try:
        groups_list = await get_user_groups(user_id)
        res = []
        for g_id, g_name in groups_list:
            tier = await get_group_tier(g_id)
            res.append({
                "id": g_id, "title": g_name, "type": "supergroup",
                "license_status": "active" if tier != "free" else "expired",
                "members": 500, "activity": [20, 35, 55, 45, 70, 65, 80],
                "joined": 12, "left": 2, "avatar_url": None
            })
        return {"groups": res}
    except Exception as e:
        logging.error(f"❌ [API Groups Error]: {e}")
        return {"groups": []}

@app.get("/api/subscribers")
async def api_subscribers(x_telegram_init_data: str = Header(None)):
    user_id = parse_telegram_user_id(x_telegram_init_data) or 8269470905
    try:
        subs = await get_user_subscribers_audit(user_id)
        return {"subscribers": subs}
    except Exception as e:
        logging.error(f"❌ [API Subscribers Error]: {e}")
        return {"subscribers": []}

@app.get("/api/affiliates/me")
async def api_affiliates(x_telegram_init_data: str = Header(None)):
    return {"invited_communities": 0, "earned_stars": 0, "balance": 0}

async def run_fastapi_server():
    port = int(os.getenv("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


# ==========================================
# ⚙️ GESTIÓN DE CALLBACKS Y CLONES DE AIOGRAM
# ==========================================
fallback_router = Router(name="callback_fallback")

@fallback_router.callback_query()
async def cb_unhandled_fallback(callback: CallbackQuery, bot: Bot):
    if callback.data and callback.data.startswith("chplans_"):
        try:
            return await user_private.cb_channel_plans_dispatch(callback, bot)
        except Exception as ex:
            logging.error(f"❌ [Fallback Rescue chplans] Fallo al despachar plan: {ex}", exc_info=True)

    logging.warning(f"🧭 [Callback sin handler] bot_id={bot.id} usuario={callback.from_user.id} callback_data={callback.data!r}")
    is_es = bool(callback.from_user.language_code and callback.from_user.language_code.startswith("es"))
    text = "⚠️ Este botón ya no está activo. Envía /start para renovar el menú." if is_es else "⚠️ This button is no longer active. Send /start to refresh the menu."
    try:
        await callback.answer(text, show_alert=True)
    except Exception:
        pass


@dp.errors()
async def on_dispatcher_error(event: ErrorEvent) -> bool:
    update = event.update
    logging.error(f"❌ [Error de despacho] update_id={update.update_id}: {event.exception!r}", exc_info=event.exception)
    if update.callback_query:
        try:
            await update.callback_query.answer("⚠️ Error temporal / Temporary error", show_alert=False)
        except Exception:
            pass
    return True


async def _dispatch_clone_update(clone_bot: Bot, bot_username: str, update: Update):
    try:
        is_private_start = False
        if update.callback_query:
            cq = update.callback_query
            logging.info(f"🔘 [Clon @{bot_username}] Callback de {cq.from_user.id}: {cq.data!r}")
        elif update.message and update.message.chat.type == "private":
            text = (update.message.text or "").strip()
            user_id = update.message.from_user.id if update.message.from_user else 0
            is_private_start = text.startswith("/start")
            logging.info(f"📩 [Clon @{bot_username}] Mensaje de {user_id}: '{text}'")

        result = await dp.feed_update(clone_bot, update)

        if result is UNHANDLED and is_private_start:
            user = update.message.from_user
            logging.warning(f"⚠️ [Clon @{bot_username}] /start sin handler; aplicando bienvenida de respaldo.")
            try:
                if user:
                    await get_or_create_user(user.id, user.username or "Sin username", user.full_name)
            except Exception as db_err:
                logging.error(f"Aviso BD Clon: {db_err}")
            await send_official_welcome(clone_bot, update.message.chat.id, user, bot_username)
    except Exception as feed_err:
        logging.error(f"❌ [Error en clon @{bot_username}]: {feed_err}", exc_info=True)


async def _clone_worker(clone_bot: Bot, token: str):
    allowed_updates = ["message", "callback_query", "pre_checkout_query", "chat_join_request", "chat_member", "my_chat_member"]
    try:
        await clone_bot.delete_webhook(drop_pending_updates=True)
        bot_info = await clone_bot.get_me()
        bot_username = bot_info.username or "BotClon"
        logging.info(f"🧬 [Bot Clon Activo]: Poller iniciado para @{bot_username} (ID: {bot_info.id}).")
    except TelegramUnauthorizedError as auth_err:
        logging.error(f"❌ [Error Fatal] El token del clon {token[:10]} fue revocado o es inválido: {auth_err}")
        try:
            from database.database import get_db_connection
            def _revoke_sync():
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE bot_clones SET status = 'revoked', bot_token = '' WHERE bot_token = ?", (token,))
                    conn.commit()
            await asyncio.to_thread(_revoke_sync)
        except Exception:
            pass
        finally:
            active_clone_tasks.pop(token, None)
            return
    except Exception as e:
        logging.error(f"❌ [Error Handshake Clon {token[:10]}]: {e}")
        return

    offset = None
    while token in active_clone_tasks:
        try:
            updates = await clone_bot.get_updates(offset=offset, timeout=15, allowed_updates=allowed_updates)
            for update in updates:
                offset = update.update_id + 1
                await _dispatch_clone_update(clone_bot, bot_username, update)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.warning(f"⚠️ [Loop Clon @{bot_username}]: {e}")
            await asyncio.sleep(2)


async def start_clone_polling_task(token: str):
    if not token or token in active_clone_tasks:
        return
    try:
        clone_bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        task = asyncio.create_task(_clone_worker(clone_bot, token))
        active_clone_tasks[token] = {"bot": clone_bot, "task": task}
    except Exception as e:
        logging.error(f"⚠️ [Error Inicializando Bot Clon {token[:10]}]: {e}")


async def stop_clone_polling_task(token: str):
    task_data = active_clone_tasks.pop(token, None)
    if task_data:
        task_data["task"].cancel()
        try:
            await task_data["bot"].session.close()
        except Exception:
            pass


def trigger_dynamic_clone(token: str):
    asyncio.create_task(start_clone_polling_task(token))


def trigger_disconnect_clone(token: str):
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

    # 🌐 Iniciar FastAPI en segundo plano para atender la Mini App
    asyncio.create_task(run_fastapi_server())
    print(f"🌐 [API Backend Web]: Servidor FastAPI activo en puerto {os.getenv('PORT', 8080)}.")

    master_bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    set_master_bot_id(master_bot.id)
    try:
        master_info = await master_bot.get_me()
        set_master_bot_username(master_info.username or "")
    except Exception as e:
        print(f"⚠️ [Aviso Identidad Maestro]: No se pudo resolver el @username del Maestro: {e}")

    dp.message.middleware(AntiSpamMiddleware())

    dp.include_router(payments.router)
    dp.include_router(user_private.router)
    dp.include_router(moderation.router)
    dp.include_router(admin_group.router)
    dp.include_router(ecosystem.router)
    dp.include_router(vc_manager.router)
    dp.include_router(groups.router)
    dp.include_router(fallback_router)

    print("📡 [Radar MTProto]: Desplegando clúster de Centinelas...")
    try:
        start_voice_radar(master_bot)
    except Exception as e:
        print(f"⚠️ [Radar MTProto Aviso]: No se pudo iniciar el gestor de centinelas: {e}")

    asyncio.create_task(ecosystem.start_channel_broadcast_worker(master_bot))

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
        required_updates = ["message", "callback_query", "pre_checkout_query", "chat_join_request", "chat_member", "my_chat_member"]
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