"""Thread-safe fan-out from the detector thread to WebSocket clients."""

from __future__ import annotations

import asyncio


class EventBus:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    def publish(self, event: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._put, event)

    def _put(self, event: dict) -> None:
        for queue in list(self._queues):
            queue.put_nowait(event)


bus = EventBus()
