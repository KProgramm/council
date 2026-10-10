# contracts/bus.py
# FROZEN after G0. Only Krish changes this file, and announces it in the group chat.
from typing import Protocol, Callable, Awaitable
from contracts.schema import Message

class Bus(Protocol):
    async def publish(self, msg: Message) -> None: ...
    def subscribe(self, handler: Callable[[Message], Awaitable[None]]) -> None: ...
