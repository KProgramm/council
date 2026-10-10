# transport/slim_bus.py — stub. Owner: Krish (G6).
from typing import Awaitable, Callable

from contracts.schema import Message


class SlimBus:
    """Bus over a Cisco AGNTCY SLIM group channel named after the review_id."""

    def __init__(self, review_id: str):
        self.review_id = review_id

    def subscribe(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        raise NotImplementedError("G6")

    async def publish(self, msg: Message) -> None:
        raise NotImplementedError("G6")
