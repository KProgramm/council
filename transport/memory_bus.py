import asyncio
from typing import Awaitable, Callable

from contracts.schema import Message


class MemoryBus:
    """In-process Bus: every subscriber hears every published Message."""

    def __init__(self):
        self.handlers: list[Callable[[Message], Awaitable[None]]] = []

    def subscribe(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        self.handlers.append(handler)

    async def publish(self, msg: Message) -> None:
        await asyncio.gather(*(handler(msg) for handler in self.handlers))
