# council/tradeoff_map.py — stub. Owner: Shiven (G3).
from contracts.schema import Message, TradeoffMap


async def build_map(transcript: list[Message], outcome: str) -> TradeoffMap:
    """One LLM call that summarizes the debate into a TradeoffMap. Never picks the answer."""
    raise NotImplementedError("G3")
