from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable

import websockets


DEFAULT_WS_BASE_URL = os.getenv("AGENT_CHAT_WS_BASE_URL", "ws://localhost:8000/ws/conversations")


EventHandler = Callable[[dict], Awaitable[None] | None]
StatusHandler = Callable[[str], Awaitable[None] | None]


class ConversationWebSocketClient:
    def __init__(self, base_url: str = DEFAULT_WS_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    async def listen(
        self,
        conversation_id: str,
        on_event: EventHandler,
        stop_event: asyncio.Event,
        on_status: StatusHandler | None = None,
    ) -> None:
        url = f"{self.base_url}/{conversation_id}"
        while not stop_event.is_set():
            try:
                if on_status is not None:
                    result = on_status(f"Connecting to websocket for {conversation_id}...")
                    if asyncio.iscoroutine(result):
                        await result

                async with websockets.connect(url) as websocket:
                    if on_status is not None:
                        result = on_status(f"Live websocket connected for {conversation_id}")
                        if asyncio.iscoroutine(result):
                            await result

                    while not stop_event.is_set():
                        try:
                            payload = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                        except TimeoutError:
                            continue

                        event = json.loads(payload)
                        result = on_event(event)
                        if asyncio.iscoroutine(result):
                            await result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if stop_event.is_set():
                    break
                if on_status is not None:
                    result = on_status(f"Websocket disconnected: {error}. Retrying...")
                    if asyncio.iscoroutine(result):
                        await result
                await asyncio.sleep(1)


class ChannelWebSocketClient:
    def __init__(self, url: str) -> None:
        self.url = url

    async def listen(
        self,
        on_event: EventHandler,
        stop_event: asyncio.Event,
        on_status: StatusHandler | None = None,
    ) -> None:
        while not stop_event.is_set():
            try:
                if on_status is not None:
                    result = on_status(f"Connecting to websocket channel {self.url}...")
                    if asyncio.iscoroutine(result):
                        await result

                async with websockets.connect(self.url) as websocket:
                    if on_status is not None:
                        result = on_status(f"Live websocket connected for channel {self.url}")
                        if asyncio.iscoroutine(result):
                            await result

                    while not stop_event.is_set():
                        try:
                            payload = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                        except TimeoutError:
                            continue

                        event = json.loads(payload)
                        result = on_event(event)
                        if asyncio.iscoroutine(result):
                            await result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if stop_event.is_set():
                    break
                if on_status is not None:
                    result = on_status(f"Websocket channel disconnected: {error}. Retrying...")
                    if asyncio.iscoroutine(result):
                        await result
                await asyncio.sleep(1)