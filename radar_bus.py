"""
radar_bus.py — The Bunker OS (FastAPI WebSockets / Aiogram 3.x / Pyrogram)

Bus de telemetría en caliente desacoplado de main.py.
Cualquier módulo (groups, assistant, ecosystem, payments) publica eventos aquí sin
importar main.py, eliminando las importaciones circulares.

Diseño:
- Cada conexión tiene su propia cola acotada y una tarea escritora dedicada. Publicar
  es O(1) y nunca espera la red: un cliente lento no frena al resto de la sala ni al
  pipeline de mensajes del bot.
- Desbordamiento: se descarta el evento más antiguo y la secuencia `seq` por sala
  permite al cliente detectar huecos y pedir un `refresh`.
- Toda la escritura sobre un WebSocket pasa por su cola (nunca hay dos `send_json`
  concurrentes sobre el mismo socket).
- Si no hay oyentes en una sala, publicar no construye ni encola nada.

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Any, Dict, Iterable, Optional, Set

logger = logging.getLogger("bunker.radar_bus")

PROTOCOL_VERSION = 2
SIGNATURE = "Cloud Media Management"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


WS_QUEUE_MAX = max(16, _env_int("WS_QUEUE_MAX", 256))
WS_SEND_TIMEOUT = max(1.0, _env_float("WS_SEND_TIMEOUT", 5.0))
WS_MAX_PER_CHAT = max(1, _env_int("WS_MAX_PER_CHAT", 200))
WS_MAX_PER_USER = max(1, _env_int("WS_MAX_PER_USER", 6))

# Eventos que siempre se entregan aunque el cliente haya filtrado su suscripción.
ALWAYS_DELIVERED: Set[str] = {"hello", "pong", "error", "analytics_snapshot", "initial_state", "state_refresh", "server_shutdown"}

_CLOSE_SENTINEL = object()


class RadarClient:
    """Conexión WebSocket registrada en una sala (chat_id)."""

    __slots__ = ("ws", "chat_id", "user_id", "queue", "writer", "events", "connected_at", "dropped", "closed")

    def __init__(self, ws: Any, chat_id: int, user_id: int):
        self.ws = ws
        self.chat_id = int(chat_id)
        self.user_id = int(user_id or 0)
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_MAX)
        self.writer: Optional[asyncio.Task] = None
        self.events: Optional[Set[str]] = None
        self.connected_at = time.time()
        self.dropped = 0
        self.closed = False


class RadarHub:
    """
    Gestor de salas WebSocket por chat_id.

    Mantiene la API pública del antiguo ConnectionManager de main.py
    (`connect`, `disconnect`, `broadcast`, `broadcast_global`, `total_connections`,
    `active_connections`) para que el código existente siga funcionando sin cambios.
    """

    def __init__(self) -> None:
        self._rooms: Dict[int, Dict[int, RadarClient]] = {}
        self._user_counts: Dict[int, int] = {}
        self._seq: Dict[int, int] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Introspección
    # ------------------------------------------------------------------
    @property
    def active_connections(self) -> Dict[int, Set[Any]]:
        """Vista de compatibilidad: {chat_id: {websocket, ...}}."""
        return {cid: {c.ws for c in room.values()} for cid, room in self._rooms.items()}

    def has_listeners(self, chat_id: int) -> bool:
        try:
            return bool(self._rooms.get(int(chat_id)))
        except (TypeError, ValueError):
            return False

    def listener_count(self, chat_id: int) -> int:
        try:
            return len(self._rooms.get(int(chat_id), {}))
        except (TypeError, ValueError):
            return 0

    def total_connections(self) -> int:
        return sum(len(room) for room in self._rooms.values())

    def current_seq(self, chat_id: int) -> int:
        return self._seq.get(int(chat_id), 0)

    def next_seq(self, chat_id: int) -> int:
        cid = int(chat_id)
        value = self._seq.get(cid, 0) + 1
        self._seq[cid] = value
        return value

    def can_accept(self, chat_id: int, user_id: int) -> Optional[str]:
        """Devuelve None si se admite la conexión o un motivo de rechazo."""
        if self.listener_count(chat_id) >= WS_MAX_PER_CHAT:
            return "room_full"
        if user_id and self._user_counts.get(int(user_id), 0) >= WS_MAX_PER_USER:
            return "too_many_sessions"
        return None

    # ------------------------------------------------------------------
    # Ciclo de vida de conexiones
    # ------------------------------------------------------------------
    async def connect(self, chat_id: int, websocket: Any, user_id: int = 0, accept: bool = True) -> RadarClient:
        if accept:
            await websocket.accept()
        self._loop = asyncio.get_running_loop()
        client = RadarClient(websocket, int(chat_id), int(user_id or 0))
        self._rooms.setdefault(client.chat_id, {})[id(websocket)] = client
        if client.user_id:
            self._user_counts[client.user_id] = self._user_counts.get(client.user_id, 0) + 1
        client.writer = asyncio.create_task(self._writer(client), name=f"radar_writer:{client.chat_id}")
        return client

    def _unregister(self, client: RadarClient) -> bool:
        room = self._rooms.get(client.chat_id)
        if not room or room.get(id(client.ws)) is not client:
            return False
        room.pop(id(client.ws), None)
        if not room:
            self._rooms.pop(client.chat_id, None)
        if client.user_id:
            remaining = self._user_counts.get(client.user_id, 0) - 1
            if remaining > 0:
                self._user_counts[client.user_id] = remaining
            else:
                self._user_counts.pop(client.user_id, None)
        client.closed = True
        return True

    def get_client(self, chat_id: int, websocket: Any) -> Optional[RadarClient]:
        return self._rooms.get(int(chat_id), {}).get(id(websocket))

    async def disconnect(self, chat_id: int, websocket: Any) -> None:
        client = self.get_client(chat_id, websocket)
        if client is None:
            return
        self._unregister(client)
        writer = client.writer
        if writer is not None and writer is not asyncio.current_task() and not writer.done():
            writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await writer

    async def close_all(self, code: int = 1001, reason: str = "server_shutdown") -> None:
        """Cierre ordenado de todas las salas (apagado del servidor)."""
        clients = [c for room in self._rooms.values() for c in room.values()]
        for client in clients:
            self._enqueue(client, {
                "v": PROTOCOL_VERSION, "event": "server_shutdown",
                "chat_id": str(client.chat_id), "timestamp": int(time.time()), "data": {}
            }, force=True)
        await asyncio.sleep(0.2)
        for client in clients:
            self._unregister(client)
            if client.writer is not None and not client.writer.done():
                client.writer.cancel()
            with contextlib.suppress(Exception):
                await client.ws.close(code=code, reason=reason)

    async def _writer(self, client: RadarClient) -> None:
        try:
            while True:
                message = await client.queue.get()
                if message is _CLOSE_SENTINEL:
                    break
                await asyncio.wait_for(client.ws.send_json(message), timeout=WS_SEND_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.debug("Escritor WS detenido (%s): %s", client.chat_id, ex)
        finally:
            if self._unregister(client):
                # El escritor falló primero: cerrar el socket desbloquea el bucle lector.
                with contextlib.suppress(Exception):
                    await client.ws.close(code=1011)

    # ------------------------------------------------------------------
    # Publicación
    # ------------------------------------------------------------------
    def _enqueue(self, client: RadarClient, message: dict, force: bool = False) -> bool:
        if client.closed:
            return False
        if not force and client.events is not None:
            event_name = message.get("event")
            if event_name not in client.events and event_name not in ALWAYS_DELIVERED:
                return False
        try:
            client.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                client.queue.get_nowait()
            client.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                client.queue.put_nowait(message)
            return True

    def set_filter(self, client: RadarClient, events: Optional[Iterable[str]]) -> None:
        client.events = set(events) if events else None

    def send_to(self, client: RadarClient, message: dict) -> bool:
        return self._enqueue(client, message, force=True)

    def _publish_now(self, chat_id: int, message: dict) -> int:
        room = self._rooms.get(int(chat_id))
        if not room:
            return 0
        delivered = 0
        for client in list(room.values()):
            if self._enqueue(client, message):
                delivered += 1
        return delivered

    def publish(self, chat_id: int, message: dict) -> int:
        """Encola `message` para todos los clientes de la sala. Seguro desde otros hilos."""
        loop = self._loop
        if loop is None:
            return 0
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not loop:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._publish_now, int(chat_id), message)
            return -1
        return self._publish_now(int(chat_id), message)

    async def broadcast(self, chat_id: int, message: dict) -> int:
        """Compatibilidad con ConnectionManager.broadcast (ya no bloquea)."""
        return self.publish(chat_id, message)

    async def broadcast_global(self, message: dict) -> int:
        total = 0
        for chat_id in list(self._rooms.keys()):
            total += self.publish(chat_id, message)
        return total


radar_hub = RadarHub()


def build_radar_envelope(chat_id: int, event: str, data: Optional[dict] = None) -> dict:
    return {
        "v": PROTOCOL_VERSION,
        "event": event,
        "chat_id": str(chat_id),
        "seq": radar_hub.next_seq(chat_id),
        "timestamp": int(time.time()),
        "data": data or {},
    }


def publish_radar_event(chat_id: int, event: str, data: Optional[dict] = None) -> int:
    """
    Publica un evento compacto en la sala `chat_id`. Síncrono y sin I/O:
    se puede llamar desde cualquier handler sin `await` ni `create_task`.
    """
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return 0
    if not radar_hub.has_listeners(cid):
        return 0
    return radar_hub.publish(cid, build_radar_envelope(cid, event, data))


async def notify_radar_ws(chat_id: int, event: str, data: Optional[dict] = None) -> int:
    """Variante awaitable de publish_radar_event (puente para ecosystem._notify_radar_ws)."""
    return publish_radar_event(chat_id, event, data)