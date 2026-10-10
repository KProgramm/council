# agents/base.py — stub. Owner: Shiven (G1).
from contracts.bus import Bus
from contracts.schema import AgentName, Message


class ReviewAgent:
    """One reviewer. Reads prompts/{name}.md every round, hears every bus message, decides whether to speak."""

    def __init__(self, name: AgentName, bus: Bus):
        self.name = name
        self.bus = bus
        self.transcript: list[Message] = []

    async def speak(self, round_num: int) -> Message | None:
        raise NotImplementedError("G1")
