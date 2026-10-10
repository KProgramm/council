# transport/memory_bus.py — stub. Owner: Shiven (G1).
from typing import Awaitable, Callable

from contracts.schema import Message


class MemoryBus:
    """In-process Bus: every subscriber hears every published Message."""

    def __init__(self):
        self.handlers: list[Callable[[Message], Awaitable[None]]] = []

    def subscribe(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        raise NotImplementedError("G1")

    async def publish(self, msg: Message) -> None:
        raise NotImplementedError("G1")
